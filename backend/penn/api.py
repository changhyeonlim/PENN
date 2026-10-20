"""FastAPI app. The map and timeline endpoints work with no model in the loop."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import db, tools
from .config import LOCAL_TZ
from .geo import locate
from . import timeutil
from .timeutil import resolve_window

app = FastAPI(title="PENN API")


def _default_window(days: int = 7) -> tuple[str, str]:
    now = timeutil.now_local()
    return now.isoformat(), (now + timedelta(days=days)).isoformat()


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/freshness")
def freshness():
    return db.query("SELECT source, max(finished_at) AS last_ok FROM penn.ingest_runs WHERE ok GROUP BY source")


@app.get("/map")
def map_layer(min_lat: float, min_lon: float, max_lat: float, max_lon: float,
              start: str | None = None, end: str | None = None):
    """Non-311 events whose centroid is in the viewport and active in the window."""
    s, e = (start, end) if start and end else _default_window()
    p = tools.window_params(s, e)
    rows = db.query(
        f"""SELECT {tools.EVENT_FIELDS} FROM penn.events FINAL
            WHERE source != 'philly_311'
              AND centroid_lat BETWEEN %(a)s AND %(b)s AND centroid_lon BETWEEN %(c)s AND %(d)s
              AND {tools.ACTIVE_FILTER} LIMIT 2000""",
        {"a": min_lat, "b": max_lat, "c": min_lon, "d": max_lon, **p},
    )
    return {"events": tools._jsonable(rows), "window": {"start": s, "end": e}}


@app.get("/events/{event_id}")
def event(event_id: str):
    rows = db.query(f"SELECT {tools.EVENT_FIELDS} FROM penn.events FINAL WHERE event_id = %(id)s", {"id": event_id})
    if not rows:
        raise HTTPException(404, "No such event")
    return tools._jsonable(rows)[0]


@app.get("/timeline")
def timeline(lat: float, lon: float, years: int = 3):
    loc = locate.from_click(lon, lat)
    end = timeutil.now_local()
    start = end.replace(year=end.year - years)
    return tools.get_location_timeline(loc.cells, start.isoformat(), end.isoformat()) | {"place": loc.label}


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
