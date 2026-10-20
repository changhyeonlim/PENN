"""FastAPI app. The map and timeline endpoints work with no model in the loop."""
from __future__ import annotations

from datetime import datetime, timedelta

import os

import h3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from . import db, overlaps, tools
from .config import LOCAL_TZ
from .geo import locate
from . import timeutil
from .timeutil import resolve_window

app = FastAPI(title="PENN API")
# The map (Next.js) calls this API from another origin. PENN_CORS_ORIGINS lists the allowed ones.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("PENN_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
# The city-wide map layer is ~2 MB of JSON and ~260 KB gzipped.
app.add_middleware(GZipMiddleware, minimum_size=1000)


def _default_window(days: int = 7) -> tuple[str, str]:
    now = timeutil.now_local()
    return now.isoformat(), (now + timedelta(days=days)).isoformat()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/freshness")
def freshness():
    """The last successful load per source, in Philadelphia time with offset: the map header's stamps."""
    return tools._jsonable(db.query(
        "SELECT source, max(finished_at) AS last_ok FROM ingest_runs WHERE ok GROUP BY source"))


# A runaway guard, not a page size: the whole city has had about 2,600 active records in a 30-day
# window. A response that hits it says so ("truncated"), so the map can warn instead of silently
# dropping records.
MAP_LIMIT = 20_000


@app.get("/map")
def map_layer(min_lat: float | None = None, min_lon: float | None = None,
              max_lat: float | None = None, max_lon: float | None = None,
              start: str | None = None, end: str | None = None):
    """Non-311 events active in the window, with their centroids. Without a box, the whole city: the
    map loads it once per window and pans and counts on the client."""
    s, e = (start, end) if start and end else _default_window()
    p = tools.window_params(s, e)
    box = None not in (min_lat, min_lon, max_lat, max_lon)
    where = "AND centroid_lat BETWEEN %(a)s AND %(b)s AND centroid_lon BETWEEN %(c)s AND %(d)s" if box else ""
    rows = db.query(
        f"""SELECT {tools.EVENT_FIELDS}, centroid_lat, centroid_lon, seg_ids FROM events FINAL
            WHERE source != 'philly_311' {where}
              AND {tools.ACTIVE_FILTER} LIMIT {MAP_LIMIT + 1}""",
        {"a": min_lat, "b": max_lat, "c": min_lon, "d": max_lon, **p},
    )
    truncated, rows = len(rows) > MAP_LIMIT, rows[:MAP_LIMIT]
    # The same event listed by visitphilly and Ticketmaster: the map draws one flag for each pair. A block
    # party permit under an announced event closure: the map draws the closure once.
    same, covered = overlaps.same_as(rows), overlaps.covered(rows)
    events = [{k: v for k, v in r.items() if k != "seg_ids"} for r in tools._jsonable(rows)]   # matching only
    return {"events": events, "truncated": truncated, "same_as": same, "covered": covered,
            "window": {"start": s, "end": e}}


@app.get("/events/{event_id}")
def event(event_id: str):
    rows = db.query(f"SELECT {tools.EVENT_FIELDS} FROM events FINAL WHERE event_id = %(id)s", {"id": event_id})
    if not rows:
        raise HTTPException(404, "No such event")
    return tools._jsonable(rows)[0]


@app.get("/timeline")
def timeline(lat: float, lon: float, years: int = 3):
    loc = locate.from_click(lon, lat)
    end = timeutil.now_local()
    start = end.replace(year=end.year - years)
    return tools.get_location_timeline(loc.cells, start.isoformat(), end.isoformat()) | {"place": loc.label}


def _view(loc: locate.Resolved) -> dict | None:
    """Center and bounds of the resolved cells, so the map can fly there, and the exact point of a
    corner or address (none for a neighborhood), so the map can mark what was asked about."""
    if not loc.cells:
        return None
    pts = [ll for c in loc.cells for ll in h3.cell_to_boundary(h3.int_to_str(c))]
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    return {"center": [sum(lons) / len(lons), sum(lats) / len(lats)],
            "bounds": [[min(lons), min(lats)], [max(lons), max(lats)]],
            "point": list(loc.point) if loc.point else None}


@app.get("/locate")
def locate_place(q: str):
    """Resolve only the place in a question (no model), so the map can move while /agent works."""
    loc = locate.from_text(q)
    if loc.method in ("outside", "none") or loc.needs_pin or not loc.cells:
        return {"found": False, "place": loc.label or None}
    win = resolve_window(q)
    return {"found": True, "place": loc.label, "view": _view(loc),
            "window": win.label if win else "the next 7 days"}


class AskBody(BaseModel):
    question: str
    lat: float | None = None
    lon: float | None = None


@app.post("/agent")
def agent(body: AskBody):
    from .agent.loop import ask

    loc = locate.from_click(body.lon, body.lat) if body.lat is not None and body.lon is not None \
        else locate.from_text(body.question)
    if loc.needs_pin:
        return {"needs_pin": True, "message": "Pick a point on the map so PENN knows where you mean."}
    win = resolve_window(body.question)
    if win is None:
        s, e = _default_window()
        start, end, label = s, e, "the next 7 days"
    else:
        start, end, label = win.start.isoformat(), win.end.isoformat(), win.label
    try:
        a = ask(body.question, loc.cells, start, end, loc.label, label)
    except NotImplementedError:
        raise HTTPException(501, "Ask PENN is not built yet (penn/agent/loop.py).")
    return {"answer": a.text, "place": loc.label, "window": label, "model": a.route.model,
            "route_reason": a.route.reason, "tools": a.tools_called, "cited": a.cited, "calls": a.calls}
