"""The four deterministic tools the agent may call. Each returns plain JSON with record IDs.

Location and time are resolved BEFORE these run (see penn.geo.locate and penn.timeutil),
so every tool takes H3 cells and ISO datetimes, never free text.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import db
from ..config import CENTER_CITY_BBOX, LOCAL_TZ
from ..ingestion import recurring_events
from .outlook import SQL_HITS, Outlook

STREET_311_TYPES = ("street_condition", "traffic", "construction_complaint")  # set by the Nano classifier
RECURRING_DIR = Path(__file__).resolve().parents[3] / "data" / "recurring_events"

EVENT_FIELDS = """
    event_id, source, event_type, title, description, organization, occupancy,
    source_url, source_authority, claim_certainty, start_date, end_date,
    start_ts, end_ts, open_ended, status, geometry_wkt
"""

ACTIVE_FILTER = """
    (
      (start_date IS NOT NULL AND start_date <= %(d1)s
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


def _jsonable(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in r.items()})
    return out


def search_events(cells: list[int], start: str, end: str, types: list[str] | None = None,
                  include_311: bool = False, limit: int = 50) -> dict:
    """Events touching the cells and active in [start, end). Permits are 'scheduled', never 'closed'."""
    sql = f"""
        SELECT {EVENT_FIELDS} FROM penn.events FINAL
        WHERE hasAny(h3_cells, %(cells)s) AND {ACTIVE_FILTER}
          {"AND event_type IN %(types)s" if types else ""}
          {"" if include_311 else "AND source != 'philly_311'"}
        ORDER BY coalesce(start_ts, toDateTime64(start_date, 3)) LIMIT %(limit)s
    """
    rows = db.query(sql, {"cells": cells, "types": types or [], "limit": limit, **window_params(start, end)})
    return {"events": _jsonable(rows), "count": len(rows)}


def get_location_timeline(cells: list[int], start: str, end: str) -> dict:
    """Monthly history for a place: 311 counts by type, plus permits and events with IDs."""
    counts = db.query(
        """
        SELECT toStartOfMonth(start_ts, 'America/New_York') AS month, event_type, count() AS n
        FROM penn.events FINAL
        WHERE source = 'philly_311' AND hasAny(h3_cells, %(cells)s)
          AND start_ts >= %(t0)s AND start_ts < %(t1)s
        GROUP BY month, event_type ORDER BY month
        """,
        {"cells": cells, **window_params(start, end)},
    )
    items = search_events(cells, start, end, include_311=False, limit=200)["events"]
    snapshot_start = db.query("SELECT nullIf(min(snapshot_date), toDate(0)) AS first FROM penn.permit_snapshots")
    return {
        "monthly_311": _jsonable(counts),
        "events": items,
        "closure_history_starts": _jsonable(snapshot_start)[0]["first"] if snapshot_start else None,
    }


def analyze_disruption(cells: list[int], start: str, end: str) -> dict:
    """Active items grouped by occupancy, so a sidewalk shed is not counted as a road closure."""
    events = search_events(cells, start, end, limit=200)["events"]
    groups: dict[str, list[str]] = {"Full Closure": [], "Partial Closure": [], "Footway": [], "Event": [], "Other": []}
    for e in events:
        key = e["occupancy"] if e["occupancy"] in groups else ("Event" if e["event_type"] == "event" else "Other")
        groups[key].append(e["event_id"])
    return {"by_occupancy": groups, "events": events,
            "hours_note": "Permits give dates only, not daily work hours."}


def _past_dates(target: date, years: int = 3) -> list[date]:
    """Same weekday as the target, every week for `years` years before it."""
    return [target - timedelta(weeks=w) for w in range(1, 52 * years + 1)]


def _center_city_cells() -> list[int]:
    from ..geo.h3util import polygon_cells
    b = CENTER_CITY_BBOX
    ring = [(b["min_lon"], b["min_lat"]), (b["max_lon"], b["min_lat"]), (b["max_lon"], b["max_lat"]),
            (b["min_lon"], b["max_lat"]), (b["min_lon"], b["min_lat"])]
    return polygon_cells([ring])


def disruption_outlook(cells: list[int], start: str, end: str) -> dict:
    s = datetime.fromisoformat(start).astimezone(LOCAL_TZ)
    e = datetime.fromisoformat(end).astimezone(LOCAL_TZ)
    h0, h1 = s.hour, (e.hour if e.date() == s.date() and e.hour > s.hour else 24)
    dates = _past_dates(s.date())
    base_cells = _center_city_cells()
    params = {"street_types": list(STREET_311_TYPES), "dates": dates, "h0": h0, "h1": h1}
    target_hits = db.query(SQL_HITS, {**params, "cells": cells})[0]["hits"]
    base_hits = db.query(SQL_HITS, {**params, "cells": base_cells})[0]["hits"]
    scheduled = [x["event_id"] for x in search_events(cells, start, end)["events"]]
    recurring = {}
    if RECURRING_DIR.exists():
        recurring = recurring_events.recurrence(recurring_events.load_all(RECURRING_DIR), set(cells))
    o = Outlook(
        target_hits=target_hits, target_pairs=len(cells) * len(dates),
        base_hits=base_hits, base_pairs=len(base_cells) * len(dates),
        past_windows=len(dates), scheduled_now=scheduled, recurring=recurring,
    )
    return o.summary()


TOOLS = {
    "search_events": search_events,
    "get_location_timeline": get_location_timeline,
    "analyze_disruption": analyze_disruption,
    "disruption_outlook": disruption_outlook,
}

_LOC = {
    "cells": {"type": "array", "items": {"type": "integer"}, "description": "H3 r9 cells from the resolved location"},
    "start": {"type": "string", "description": "ISO datetime with offset, window start"},
    "end": {"type": "string", "description": "ISO datetime with offset, window end (exclusive)"},
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_events",
        "description": "Permits, curated events and other items touching the location and active in the window.",
        "parameters": {"type": "object", "required": ["cells", "start", "end"], "properties": {
            **_LOC, "types": {"type": "array", "items": {"type": "string"}},
            "include_311": {"type": "boolean"}}}}},
    {"type": "function", "function": {
        "name": "get_location_timeline",
        "description": "History of the location: monthly 311 counts and past permits and events.",
        "parameters": {"type": "object", "required": ["cells", "start", "end"], "properties": _LOC}}},
    {"type": "function", "function": {
        "name": "analyze_disruption",
        "description": "Active items grouped by occupancy type (full closure, partial, footway, event).",
        "parameters": {"type": "object", "required": ["cells", "start", "end"], "properties": _LOC}}},
    {"type": "function", "function": {
        "name": "disruption_outlook",
        "description": "Past-window rate for this place vs the Center City base rate, sample sizes, and recurring-event history.",
        "parameters": {"type": "object", "required": ["cells", "start", "end"], "properties": _LOC}}},
]
