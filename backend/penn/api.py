"""FastAPI app. The map and timeline endpoints work with no model in the loop."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache

import os

import h3
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from . import db, overlaps, tools
from .agent import ambiguity, citations
from .config import LOCAL_TZ
from .geo import landmarks, locate
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


@lru_cache(maxsize=1)
def records_start() -> date:
    """Jan 1 of the year of PENN's first record (2016 when this was written)."""
    try:
        rows = db.query("SELECT min(toYear(coalesce(start_date, toDate(start_ts)))) AS y FROM events FINAL")
        return date(int(rows[0]["y"]), 1, 1)
    except Exception:
        return date(2016, 1, 1)


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


class Turn(BaseModel):
    question: str
    answer: str = ""


class AskBody(BaseModel):
    question: str
    lat: float | None = None
    lon: float | None = None
    history: list[Turn] = []        # earlier turns of this conversation, oldest first
    context: dict | None = None     # the last reply's "context", sent back as is


def _reuse_note(ctx, place, win) -> str | None:
    """Say so whenever the answer leaned on the last question's place or time, so a wrong
    carry-over is visible instead of silently answered."""
    used = [x.label for x, id_ in ((place, "prev_place"), (win, "prev_window")) if x is not None and x.id == id_]
    if not used:
        return None
    return f"Using {' and '.join(used)} from your last question."


def _next_context(place, win, previous: dict | None) -> dict:
    """What the frontend sends back next turn: the place and window this answer used, or the
    previous ones when it searched nothing (a greeting must not forget where we were)."""
    prev = previous or {}
    out = {}
    out["place"] = {**place.query, "label": place.label} if place else prev.get("place")
    out["window"] = ({"start": win.window.start.isoformat(), "end": win.window.end.isoformat(), "label": win.label}
                     if win else prev.get("window"))
    return {k: v for k, v in out.items() if v}


@app.post("/agent")
def agent(body: AskBody):
    from .agent.context import Context
    from .agent.loop import GIVE_UP, TIMED_OUT, ask

    ctx = Context(timeutil.now_local(), records_start())
    if body.lat is not None and body.lon is not None:
        loc = ctx.add_pin(body.lon, body.lat)
        if loc.method == "outside":
            return {"outside": True, "place": loc.label,
                    "message": f"PENN covers the City of Philadelphia only, and {loc.label} is outside it, "
                               "so PENN has no records to answer from."}
    ctx.add_message(body.question)
    ctx.add_previous(body.context)
    try:
        a = ask(body.question, ctx, history=[(t.question, t.answer) for t in body.history])
    except NotImplementedError:
        raise HTTPException(501, "Ask PENN is not built yet (penn/agent/loop.py).")

    place, win = ctx.last_place(), ctx.last_window()
    notes = []
    if note := _reuse_note(ctx, place, win):
        notes.append(note)
    if place:
        notes += ambiguity.notes(body.question, place.label)
    if win is not None and win.id == "default_window" and timeutil.mentions_time(body.question):
        notes.insert(0, "PENN could not read the date in your question, so this answer covers the next 7 days. "
                        "Try a form like \"November 22\", \"the 2nd week of November\" or \"March 2025\".")
    answer = "\n".join(f"Note: {n}" for n in notes) + "\n\n" + a.text if notes else a.text
    cited, past = citations.expand_groups(list(a.cited), a.groups), []   # a cited group highlights all its blocks
    if place and win and win.kind == "future" and win.id != "default_window":
        # Past years on the same dates, added in code so the model can neither drop nor pick the wrong ones.
        token = tools.LEGS.set(place.loc.seg_ids or None)
        try:
            past = tools.history.same_dates(
                tools.get_location_history(place.loc.cells, win.window.start.isoformat(), win.window.end.isoformat()),
                win.window.dates()[0], win.window.dates()[-1])
        finally:
            tools.LEGS.reset(token)
        if text := tools.history.same_dates_text(past, win.window.start.year):
            answer += "\n\n" + text
            cited += [y["cite"][4:-1] for m in past for y in m["years"]]
    # Web pages the answer cites (p5-09): shown as link chips, never drawn on the map, so they leave `cited`.
    web = [{"id": i, "title": ctx.web_pages[i].get("title", ""), "url": ctx.web_pages[i]["url"],
            "site": ctx.web_pages[i].get("site", "")} for i in dict.fromkeys(cited) if i in ctx.web_pages]
    cited = [i for i in cited if not i.startswith("web:")]
    # Landmarks the answer (then the question) names (p3-10): when the question named no place, the map
    # frames them and pins each one; with a place, the map keeps the place.
    # The hallucination guard, as the answer shows it: how many records it cites (every one matched to this
    # turn's tool results by citations.check), whether code resolved the place and dates, and each draft a
    # check sent back. None when there is no answer to vouch for.
    guard = None if a.text in (GIVE_UP, TIMED_OUT) else {
        "cited": len(set(a.cited)), "resolved_in_code": bool(ctx.searches), "sent_back": a.checks}
    named = landmarks.find(f"{answer} {body.question}")
    marks = [{"name": l.name, "point": list(l.point), "bounds": [list(c) for c in l.bounds]} for l in named[:8]]
    return {"answer": answer, "notes": notes, "past_same_dates": past, "web": web, "landmarks": marks,
            "place": place.label if place else None, "window": win.label if win else None,
            "window_kind": win.kind if win else None,
            "start": win.window.start.isoformat() if win else None, "end": win.window.end.isoformat() if win else None,
            "view": _view(place.loc) if place else None, "model": a.route.model,
            "route_reason": a.route.reason, "tools": a.tools_called, "cited": cited, "calls": a.calls, "guard": guard,
            "asked_back": not ctx.searches,          # nothing searched: PENN asked or chatted; offer a pin
            "context": _next_context(place, win, body.context)}
