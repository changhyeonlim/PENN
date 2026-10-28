"""FastAPI app. The map endpoints work with no model in the loop; /agent is Ask PENN."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache

import os
import time

import h3
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db, guardrails, overlaps, tools
from .agent import ambiguity, citations
from .config import LOCAL_TZ
from .geo import adjacent, landmarks, locate
from . import timeutil
from .timeutil import resolve_window

app = FastAPI(title="PENN API")
# The map (Next.js) calls this API from another origin. PENN_CORS_ORIGINS lists the allowed ones.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("PENN_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-PENN-Judge"],
)
# The city-wide map layer is ~2 MB of JSON and ~260 KB gzipped.
app.add_middleware(GZipMiddleware, minimum_size=1000)


# The public demo's guardrails (penn/guardrails.py; on with PENN_PUBLIC=1): every route is limited per visitor
# here, and /agent adds its own layers below.
@app.middleware("http")
async def _per_visitor(request: Request, call_next):
    if guardrails.PUBLIC:
        try:
            guardrails.check_request(guardrails.client_ip(request), request.url.path)
        except guardrails.Limited as e:
            return JSONResponse({"detail": e.message}, status_code=e.status)
    return await call_next(request)


@app.exception_handler(guardrails.Limited)
def _limited(request: Request, e: guardrails.Limited):
    return JSONResponse({"detail": e.message}, status_code=e.status)


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
    """Events active in the window, with their centroids. Without a box, the whole city: the map loads
    it once per window and pans and counts on the client."""
    s, e = (start, end) if start and end else _default_window()
    p = tools.window_params(s, e)
    box = None not in (min_lat, min_lon, max_lat, max_lon)
    where = "AND centroid_lat BETWEEN %(a)s AND %(b)s AND centroid_lon BETWEEN %(c)s AND %(d)s" if box else ""
    rows = db.query(
        f"""SELECT {tools.EVENT_FIELDS}, centroid_lat, centroid_lon, seg_ids FROM events FINAL
            WHERE {tools.ACTIVE_FILTER} {where} LIMIT {MAP_LIMIT + 1}""",
        {"a": min_lat, "b": max_lat, "c": min_lon, "d": max_lon, **p},
    )
    truncated, rows = len(rows) > MAP_LIMIT, rows[:MAP_LIMIT]
    # The same event listed by visitphilly and Ticketmaster: the map draws one flag for each pair. A block
    # party permit under an announced event closure: the map draws the closure once.
    same, covered = overlaps.same_as(rows), overlaps.covered(rows)
    events = [{k: v for k, v in r.items() if k != "seg_ids"} for r in tools._jsonable(rows)]   # matching only
    return {"events": events, "truncated": truncated, "same_as": same, "covered": covered,
            "window": {"start": s, "end": e}}


# What makes a day busy, for the map's replay slider: things people go to and the street closures they bring.
# Listings (visitphilly), ticketed shows, every street closed by a City event notice, and block parties. Records
# lasting more than a month (an exhibition, a season) count on no day: they say nothing about one day.
BUSY_SQL = """
    SELECT day,
           countIf(source = 'visitphilly') AS listings,
           countIf(source = 'ticketmaster') AS ticketed,
           countIf(source = 'event_closures') AS closures,
           uniqExactIf(splitByChar(':', title)[1], source = 'event_closures') AS notices,
           countIf(event_type = 'block_party') AS block_parties,
           count() AS total
    FROM (
        SELECT source, event_type, title,
               coalesce(start_date, toDate(start_ts, 'America/New_York')) AS d0,
               coalesce(end_date, toDate(coalesce(end_ts, start_ts), 'America/New_York'), d0) AS d1
        FROM events FINAL
        WHERE source IN ('visitphilly', 'ticketmaster', 'event_closures') OR event_type = 'block_party'
    )
    ARRAY JOIN arrayMap(i -> addDays(toDate(%(start)s), i), range(toUInt32(dateDiff('day', toDate(%(start)s), toDate(%(end)s))))) AS day
    WHERE d0 <= day AND d1 >= day AND dateDiff('day', d0, d1) <= 31
    GROUP BY day ORDER BY day
"""


@app.get("/busiest")
def busiest(start: str, end: str):
    """Each day in [start, end) (YYYY-MM-DD) with its listings, ticketed shows, event street closures (and the
    City notices behind them) and block parties, and the busiest day by their total. The replay slider draws
    the days as bars and opens on the busiest."""
    try:
        s, e = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "start and end are dates, YYYY-MM-DD")
    if not 0 < (e - s).days <= 92:
        raise HTTPException(400, "end must be after start, at most 92 days")
    days = tools._jsonable(db.query(BUSY_SQL, {"start": s.isoformat(), "end": e.isoformat()}))
    top = max(days, key=lambda d: d["total"], default=None)
    return {"days": days, "busiest": top["day"] if top else None}


class IdsBody(BaseModel):
    ids: list[str]


# One answer can light a whole route (a Marathon edition is ~80 blocks); the map asks for them in one request.
EVENTS_BATCH_MAX = 200


@app.post("/events")
def events_batch(body: IdsBody):
    """Several records by id, in the order asked; ids PENN has no record of are left out."""
    ids = list(dict.fromkeys(body.ids))
    if len(ids) > EVENTS_BATCH_MAX:
        raise HTTPException(413, f"At most {EVENTS_BATCH_MAX} ids at a time")
    if not ids:
        return []
    rows = db.query(f"SELECT {tools.EVENT_FIELDS} FROM events FINAL WHERE event_id IN %(ids)s", {"ids": ids})
    at = {i: n for n, i in enumerate(ids)}
    return sorted(tools._jsonable(rows), key=lambda r: at[r["event_id"]])


@app.get("/events/{event_id}")
def event(event_id: str):
    rows = db.query(f"SELECT {tools.EVENT_FIELDS} FROM events FINAL WHERE event_id = %(id)s", {"id": event_id})
    if not rows:
        raise HTTPException(404, "No such event")
    return tools._jsonable(rows)[0]


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
def agent(body: AskBody, request: Request):
    if not guardrails.PUBLIC:
        return _answer(body)
    ip, started, pool = guardrails.client_ip(request), time.monotonic(), "visitor"
    try:
        pool = guardrails.role(request, ip)
        body.history, body.context = guardrails.trim(body.question, body.history, body.context)
        guardrails.check_ask(ip, pool)
        key = guardrails.cache_key(body.question, body.lat, body.lon, body.history, body.context)
        if (hit := guardrails.cached(key)) is not None:
            guardrails.record(ip, "cached", started, body.question, pool=pool)
            return hit
        with guardrails.fresh_answer(pool):
            out = _answer(body)
    except guardrails.Limited as e:
        guardrails.record(ip, e.reason, started, body.question, pool=pool)
        raise
    real = out.get("guard") is not None     # a real answer, not a give-up, time-out, outage or "outside the city"
    if real:
        guardrails.store(key, out)
    guardrails.record(ip, "fresh" if real else "no_answer", started, body.question, out, pool=pool)
    return out


@app.post("/judge")
def judge(request: Request):
    """The judge gate's check of an access code (header X-PENN-Judge): 200 when it's right. The page then sends
    the code with every question, and /agent checks it again each time; this route only answers the gate."""
    if guardrails.role(request, guardrails.client_ip(request)) != "judge":
        raise guardrails.Limited(401, guardrails.BAD_CODE, "bad_code")
    return {"ok": True}


def _answer(body: AskBody) -> dict:
    from .agent.context import Context
    from .agent.loop import GIVE_UP, TIMED_OUT, UNAVAILABLE, ask

    ctx = Context(timeutil.now_local(), records_start())
    if body.lat is not None and body.lon is not None:
        loc = ctx.add_pin(body.lon, body.lat)
        if loc.method == "outside":
            return {"outside": True, "place": loc.label,
                    "message": f"PENN covers the City of Philadelphia only, and {loc.label} is outside it, "
                               "so PENN has no records to answer from."}
    ctx.add_message(body.question)
    ctx.add_previous(body.context)
    a = ask(body.question, ctx, history=[(t.question, t.answer) for t in body.history])

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
    # What the place search found beside the place (its streets, within reach of its outline) and the answer did
    # not cite: the map lights these too, after the cited ones; the headline still counts only the cited.
    nearby = adjacent.beside(ctx.found, place, set(cited))
    # Landmarks the answer (then the question) names (p3-10): when the question named no place, the map
    # frames them and pins each one; with a place, the map keeps the place.
    # The hallucination guard, as the answer shows it: how many records it cites (every one matched to this
    # turn's tool results by citations.check), whether code resolved the place and dates, and each draft a
    # check sent back. None when there is no answer to vouch for.
    guard = None if a.text in (GIVE_UP, TIMED_OUT, UNAVAILABLE) else {
        "cited": len(set(a.cited)), "resolved_in_code": bool(ctx.searches), "sent_back": a.checks}
    named = landmarks.find(f"{answer} {body.question}")
    marks = [{"name": l.name, "point": list(l.point), "bounds": [list(c) for c in l.bounds]} for l in named[:8]]
    return {"answer": answer, "notes": notes, "past_same_dates": past, "web": web, "landmarks": marks,
            "place": place.label if place else None, "window": win.label if win else None,
            "window_kind": win.kind if win else None,
            "start": win.window.start.isoformat() if win else None, "end": win.window.end.isoformat() if win else None,
            "view": _view(place.loc) if place else None, "model": a.route.model,
            "route_reason": a.route.reason, "tools": a.tools_called, "cited": cited, "nearby": nearby, "calls": a.calls, "guard": guard,
            # nothing searched: PENN asked or chatted; offer a pin (not after a give-up, time-out or outage)
            "asked_back": not ctx.searches and guard is not None,
            "context": _next_context(place, win, body.context)}
