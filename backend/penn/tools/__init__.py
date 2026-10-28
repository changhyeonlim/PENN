"""The deterministic tools the agent may call. Each returns plain JSON with record IDs.

    search_events          what is in effect here, in this window (now)
    get_location_history   which events closed streets here in past years (then)

Location and time are resolved BEFORE these run (see penn.geo.locate and penn.timeutil),
so every tool takes H3 cells and ISO datetimes, never free text.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone

from .. import db
from ..config import LOCAL_TZ
from . import history

# The exact legs (centerline seg_ids) of a resolved intersection, set by the API or the eval runner
# around one question. Records that carry seg_ids then match only on those legs; records without
# them (events, closures not placed on the centerline) still match by cells. The model never
# handles the list: it is server-side context, like the resolved cells.
LEGS: ContextVar[list[int] | None] = ContextVar("penn_legs", default=None)
# A neighborhood's outline in fine cells (locate.area_inside), set the same way: the search still matches by
# the area's r9 cells, and the brief then keeps to the streets inside the outline.
AREA: ContextVar[frozenset[int] | None] = ContextVar("penn_area", default=None)
# A place's outline reached out a block (geo.adjacent.box, west/south/east/north), set the same way for a place
# answer: records whose shape enters it are the place's own; the rest of the search is counted only.
NEAR: ContextVar[tuple[float, float, float, float] | None] = ContextVar("penn_near", default=None)


def place_filter(alias: str = "") -> str:
    a = f"{alias}." if alias else ""
    if LEGS.get():
        return f"(hasAny({a}seg_ids, %(legs)s) OR (empty({a}seg_ids) AND hasAny({a}h3_cells, %(cells)s)))"
    return f"hasAny({a}h3_cells, %(cells)s)"


def place_params() -> dict:
    return {"legs": LEGS.get() or []}


# Every event_type in penn.events (lane closures after p2-11, curated and listed events).
EVENT_TYPES = ["utility_work", "roadwork", "construction", "block_party", "parking_restriction", "event",
               "other"]

EVENT_FIELDS = """
    event_id, source, event_type, title, description, organization, occupancy,
    source_url, source_authority, claim_certainty, start_date, end_date,
    start_ts, end_ts, open_ended, status, geometry_wkt
"""
# What the model sees: the same fields minus the shape. geometry_wkt was half of every
# tool result (~35k chars for 47 permits) and the model never needs it; the map gets
# shapes from /map and /events/{id}.
TOOL_FIELDS = EVENT_FIELDS.replace(", geometry_wkt", "")
assert "geometry_wkt" not in TOOL_FIELDS

# A record with an end time is active until that moment, by its times alone: an announced closure carries its
# day as well, and matched by the day it stayed "active" all day after it ended (the Market Street Run for the
# Blue, 7:30 to 10:30 AM, still on the map at noon). Without an end time (a show at 7 PM, a permit), the day decides.
ACTIVE_FILTER = """
    (
      (end_ts IS NULL AND start_date IS NOT NULL AND start_date <= %(d1)s
        AND (end_date IS NULL OR open_ended OR end_date >= %(d0)s))
      OR
      (start_ts IS NOT NULL AND start_ts < %(t1)s AND coalesce(end_ts, start_ts) >= %(t0)s)
    )
"""


def window_params(start: str, end: str) -> dict:
    """Local calendar dates for date-only rows, UTC instants for timestamped rows."""
    t0 = datetime.fromisoformat(start).astimezone(LOCAL_TZ)
    t1 = datetime.fromisoformat(end).astimezone(LOCAL_TZ)
    last = (t1 - timedelta(microseconds=1)).date()
    return {"t0": t0.astimezone(timezone.utc), "t1": t1.astimezone(timezone.utc), "d0": t0.date(), "d1": last}


def _iso(v):
    """Timestamps leave as Philadelphia local time with offset: the model reads 11:00 UTC as
    "11 a.m.", so it must never see UTC. Dates are calendar dates and stay as they are."""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)   # ClickHouse DateTime64(3, 'UTC')
        return v.astimezone(LOCAL_TZ).isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def _jsonable(rows: list[dict]) -> list[dict]:
    return [{k: _iso(v) for k, v in r.items()} for r in rows]


BRIEF_LIMIT = 400   # a brief sends counts for most rows, so it can afford to read many more


DURATION = "coalesce(dateDiff('day', start_date, end_date), dateDiff('day', start_ts, end_ts), 100000)"


def search_events(cells: list[int], start: str, end: str, types: list[str] | None = None,
                  limit: int = 100, detail: str = "full", max_days: int | None = None) -> dict:
    """Events touching the cells and active in [start, end). Permits are 'scheduled', never 'closed'.

    max_days keeps only records lasting at most that many calendar days (a one-day permit is 1);
    open-ended ones never qualify.

    detail="full" lists every record. detail="brief" sorts them into what matters for a trip
    (events, closures that change getting there, a count of the rest): see penn.agent.impact.
    detail="place" sorts the records at the place (NEAR) by kind, biggest first: impact.place.

    Shortest first: a one-day loading zone says more about "this Saturday" than a year-long
    sidewalk shed, and with a limit the long ones are the ones to drop. Open-ended permits go
    last. `truncated` tells the model more matches exist, so it never claims a complete list.
    """
    brief = detail in ("brief", "place")
    if brief:
        limit = max(limit, BRIEF_LIMIT)
    extra = ", seg_ids, geometry_wkt" if detail == "place" else ", seg_ids" if brief else ""
    sql = f"""
        SELECT {TOOL_FIELDS}{extra} FROM events FINAL
        WHERE {place_filter()} AND {ACTIVE_FILTER}
          {"AND event_type IN %(types)s" if types else ""}
          {f"AND NOT open_ended AND {DURATION} < %(max_days)s" if max_days else ""}
        ORDER BY open_ended, {DURATION}, coalesce(start_ts, toDateTime64(start_date, 3))
        LIMIT %(limit_plus)s
    """
    params = window_params(start, end)
    rows = db.query(sql, {"cells": cells, "types": types or [], "limit_plus": limit + 1, "max_days": max_days or 0,
                          **params, **place_params()})
    truncated = len(rows) > limit
    rows = rows[:limit]
    if detail == "place":
        from ..agent import impact
        out = impact.place(_jsonable(rows), start, NEAR.get(), LEGS.get()) | {"count": len(rows), "truncated": truncated}
    elif brief:
        from ..agent import impact
        out = impact.brief(_jsonable(rows), start, inside=AREA.get()) | {"count": len(rows), "truncated": truncated}
    else:
        out = {"events": _jsonable(rows), "count": len(rows), "truncated": truncated}
    # The model often answers "in past years" / "will X close" questions from this tool alone and
    # never sees the history. Point it there when history exists for this place.
    # The model often answers "in past years" / "will X close" questions from this tool alone and
    # never calls get_location_history, so a compact digest of past event closures here rides along:
    # each event, the years it closed streets here, and one citable record per year.
    past = db.query(HISTORY_SQL.format(place=place_filter("e")), {"cells": cells, "since": 2016, "before": params["d0"], **place_params()})
    if past:
        out["past_events_here"] = history.digest(_jsonable(past), history.catalog())
    return out


HISTORY_SQL = """
    SELECT e.event_id AS event_id, c.series AS series, c.year AS year, c.start_date AS date,
           c.street AS street, c.from_street AS from_street, c.to_street AS to_street,
           e.start_ts AS start, e.end_ts AS end, c.conditional AS conditional
    FROM events AS e FINAL
    INNER JOIN (SELECT * FROM event_closures FINAL) AS c ON c.closure_id = e.source_record_id
    WHERE e.source = 'event_closures' AND {place}
      AND c.year >= %(since)s AND c.start_date < %(before)s
    ORDER BY c.series, c.start_date, e.start_ts
"""
COVERAGE_SQL = """
    SELECT uniqExact(series) AS events_in_penn, min(year) AS first_year, max(year) AS last_year
    FROM event_closures FINAL WHERE geocoded
"""


def get_location_history(cells: list[int], start: str, end: str, since_year: int = 2016) -> dict:
    """Past announced event closures touching the cells, grouped by event and year.
    Only closures that started before the window ends, so nothing from after the question leaks in."""
    before = window_params(start, end)["d1"] + timedelta(days=1)
    rows = _jsonable(db.query(HISTORY_SQL.format(place=place_filter("e")),
                              {"cells": cells, "since": since_year, "before": before, **place_params()}))
    cov = _jsonable(db.query(COVERAGE_SQL))
    return history.summarize(rows, history.catalog(), cov[0] if cov else {})


def _search_for_model(**kw) -> dict:
    """The model's search_events: a brief unless it asks for the full list."""
    return search_events(**{"detail": "brief", **kw})


TOOLS = {
    "search_events": _search_for_model,
    "get_location_history": get_location_history,
}

_LOC = {
    "cells": {"type": "array", "items": {"type": "integer"}, "description": "H3 r9 cells from the resolved location"},
    "start": {"type": "string", "description": "ISO datetime with offset, window start"},
    "end": {"type": "string", "description": "ISO datetime with offset, window end (exclusive)"},
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_events",
        "description": "Permits, curated events and other items touching the location and active in the window. If truncated is true, more matches exist: say the list is partial or narrow it with types.",
        "parameters": {"type": "object", "required": ["cells", "start", "end"], "properties": {
            **_LOC,
            "detail": {"type": "string", "enum": ["brief", "full"],
                       "description": "brief (default): for questions about an area or a trip (what is going on, "
                                      "can I get there): events, closures that change getting there, and only a "
                                      "count of smaller permits. full: every record, for questions about permits, "
                                      "one block or address, parking, construction, or a specific record."},
            "types": {"type": "array", "items": {"type": "string", "enum": EVENT_TYPES},
                              "description": "Optional filter. Leave it out unless the user asks for one kind only; "
                                             "parking_restriction covers Temporary No Parking and loading zones."}}}}},
    {"type": "function", "function": {
        "name": "get_location_history",
        "description": "Past City-announced event closures at this location (races, parades, festivals, "
                       "one-off events), grouped by event and year, with times. Use it for questions about past "
                       "years, whether something happens every year, or what usually closes here. recurrence "
                       "says whether an event is annual, irregular or one_off; coverage says what PENN's "
                       "records include.",
        "parameters": {"type": "object", "required": ["cells", "start", "end"], "properties": {
            **_LOC, "since_year": {"type": "integer", "description": "Earliest year to include (default 2016)"}}}}},
]
