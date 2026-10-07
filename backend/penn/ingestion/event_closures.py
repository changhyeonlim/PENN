"""Announced street closures for events, from the city's closure notices.

Input: data/event_closures/*.csv, one row per closed street segment. Columns:

    series        stable slug, e.g. "midnight-run" (required)
    year          int (required)
    date          "YYYY-MM-DD", local first day of the closure (required)
    street        the closed street or block, as the notice words it (required)
    from_street   segment start (optional)
    to_street     segment end (optional)
    closure_start "HH:MM" 24-hour local (optional)
    closure_end   "HH:MM" 24-hour local (optional)
    end_date      "YYYY-MM-DD", local last day (optional)
    conditional   "yes" if the notice says the street closes only if needed (optional)
    notes         free text (optional)
    source_url    the phila.gov notice the row came from (required)

end_date, conditional and notes came later, so files without them still load.

Rules:
    - source_url must start with https://www.phila.gov/.
    - An end_date earlier than date is an error.
    - start_ts is date + closure_start; end_ts is (end_date or date) + closure_end. Both are
      Philadelphia time, stored as UTC.
    - No closure_end: end_ts stays empty, meaning "until the event ends". Never guess a time.
    - No end_date and closure_end earlier than closure_start: the closure runs past midnight
      (Midnight Run). end_ts moves to the next day and a warning is logged.
    - No end_date and closure_end equal to closure_start: an error, since it could mean zero
      hours or a full day (the Unity Concert rows before end_date was added).
    - closure_id is series-date-hash(street, from_street, to_street), so a rerun replaces rows
      in the ReplacingMergeTree instead of adding copies. Two rows with the same id are an error.

load_all returns (rows, errors). Rows are dicts keyed by event_closures columns; errors are
(file, line, reason), where line is the row's line in the CSV, header = 1.
"""
from __future__ import annotations

import csv
import hashlib
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

from ..config import LOCAL_TZ
from ..geo.h3util import centroid, line_cells, multiline_wkt, point_cell
from ..models import NormalizedEvent

log = logging.getLogger(__name__)

SOURCE = "event_closures"

REQUIRED = ("series", "year", "date", "street", "source_url")
SOURCE_PREFIX = "https://www.phila.gov/"
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
TRUE = {"yes", "true", "1"}
FALSE = {"", "no", "false", "0"}


class RowError(NamedTuple):
    file: str
    line: int
    reason: str


def closure_id(series: str, d: str, street: str, from_street: str, to_street: str) -> str:
    h = hashlib.sha1(f"{street}|{from_street}|{to_street}".encode()).hexdigest()[:8]
    return f"{series}-{d}-{h}"


def _date(value: str, col: str) -> date:
    if not DATE_RE.fullmatch(value):
        raise ValueError(f"{col} {value!r} is not YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{col} {value!r} is not a real date") from None


def _time(value: str, col: str) -> time | None:
    if not value:
        return None
    if not TIME_RE.fullmatch(value):
        raise ValueError(f"{col} {value!r} is not 24-hour HH:MM")
    return time.fromisoformat(value)


def _utc(d: date, t: time) -> datetime:
    return datetime.combine(d, t, LOCAL_TZ).astimezone(timezone.utc)


def parse_row(p: dict[str, str], where: str) -> dict:
    """Validate one CSV row and return it as an event_closures row. Raises ValueError."""
    p = {k: (v or "").strip() for k, v in p.items() if k is not None}
    missing = [k for k in REQUIRED if not p.get(k)]
    if missing:
        raise ValueError(f"missing {', '.join(missing)}")
    if not p["source_url"].startswith(SOURCE_PREFIX):
        raise ValueError(f"source_url must start with {SOURCE_PREFIX}")
    try:
        year = int(p["year"])
    except ValueError:
        raise ValueError(f"year {p['year']!r} is not a number") from None
    d = _date(p["date"], "date")
    d_end = _date(p["end_date"], "end_date") if p.get("end_date") else None
    if d_end and d_end < d:
        raise ValueError(f"end_date {d_end} is before date {d}")
    t_start = _time(p.get("closure_start", ""), "closure_start")
    t_end = _time(p.get("closure_end", ""), "closure_end")
    cond = p.get("conditional", "").lower()
    if cond not in TRUE | FALSE:
        raise ValueError(f"conditional {p['conditional']!r} is not yes or no")

    last = d_end or d
    if t_start and t_end and not d_end:
        if t_end == t_start:
            raise ValueError(f"closure_end equals closure_start ({t_start:%H:%M}) with no end_date")
        if t_end < t_start:
            last = d + timedelta(days=1)
            log.warning("%s: closure_end %s is before closure_start %s; reading it as the next day",
                        where, p["closure_end"], p["closure_start"])
    start_ts = _utc(d, t_start) if t_start else None
    end_ts = _utc(last, t_end) if t_end else None
    if start_ts and end_ts and end_ts <= start_ts:
        raise ValueError(f"ends at {last} {t_end:%H:%M}, not after it starts")

    return {
        "closure_id": closure_id(p["series"], p["date"], p["street"],
                                 p.get("from_street", ""), p.get("to_street", "")),
        "series": p["series"],
        "year": year,
        "start_date": d,
        "end_date": last,
        "street": p["street"],
        "from_street": p.get("from_street", ""),
        "to_street": p.get("to_street", ""),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "conditional": cond in TRUE,
        "notes": p.get("notes", ""),
        "source_url": p["source_url"],
    }


def load_file(path: Path) -> tuple[list[dict], list[RowError]]:
    rows: list[dict] = []
    errors: list[RowError] = []
    seen: dict[str, int] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        absent = [k for k in REQUIRED if k not in (reader.fieldnames or [])]
        if absent:
            return [], [RowError(path.name, 1, f"header missing columns {', '.join(absent)}")]
        for p in reader:
            line = reader.line_num
            if not any((v or "").strip() for v in p.values() if isinstance(v, str)):
                continue
            try:
                row = parse_row(p, f"{path.name}:{line}")
            except ValueError as e:
                errors.append(RowError(path.name, line, str(e)))
                continue
            if row["closure_id"] in seen:
                errors.append(RowError(path.name, line, f"duplicate of line {seen[row['closure_id']]}"))
                continue
            seen[row["closure_id"]] = line
            rows.append(row)
    return rows, errors


def load_all(folder: Path) -> tuple[list[dict], list[RowError]]:
    rows: list[dict] = []
    errors: list[RowError] = []
    for path in sorted(folder.glob("*.csv")):
        r, e = load_file(path)
        rows.extend(r)
        errors.extend(e)
    return rows, errors


# Stage 2: rows matched to street centerline segments (geo/streets.py) become map events.


def _series_title(series: str) -> str:
    return series.replace("-", " ").title()


def has_ended(row: dict, now: datetime) -> bool:
    """end_ts if known, else the end of end_date in Philadelphia."""
    end = row.get("end_ts")
    if end is not None:
        if end.tzinfo is None:  # clickhouse-connect may hand back naive UTC
            end = end.replace(tzinfo=timezone.utc)
        return end <= now
    return _utc(row["end_date"] + timedelta(days=1), time(0, 0)) <= now


def to_event(row: dict, segments: list, notes: list[str], now: datetime) -> NormalizedEvent:
    """One event_closures row plus its centerline segments -> one map event.

    claim_certainty is "observed" once the closure has ended, "scheduled" before that.
    The notice's own wording stays in the title; segment ids go in the description so the
    match can be checked against the centerline (and Lane Closures, which shares seg_id).
    """
    lines = [list(s.coords) for s in segments]
    lon, lat = centroid([pt for ln in lines for pt in ln])
    cells: list[int] = []
    for ln in lines:
        for c in line_cells(ln):
            if c not in cells:
                cells.append(c)
    where = row["street"]
    if row.get("from_street") and row.get("to_street"):
        where += f", {row['from_street']} to {row['to_street']}"
    desc = [f"Street closure announced by the City for {_series_title(row['series'])} {row['year']}."]
    if row.get("conditional"):
        desc.append("Closes only if conditions warrant.")
    if row.get("notes"):
        desc.append(row["notes"])
    desc.append("Centerline segments: " + ", ".join(str(s.seg_id) for s in segments) + ".")
    desc += [f"Matching note: {n}." for n in notes]
    return NormalizedEvent(
        source=SOURCE,
        source_record_id=row["closure_id"],
        event_type="event",
        title=f"{_series_title(row['series'])}: {where}",
        description=" ".join(desc),
        organization="City of Philadelphia",
        source_url=row["source_url"],
        source_authority="curated",
        claim_certainty="observed" if has_ended(row, now) else "scheduled",
        geometry_wkt=multiline_wkt(lines),
        centroid_lat=lat,
        centroid_lon=lon,
        h3_r9=point_cell(lon, lat),
        h3_cells=cells,
        start_date=row["start_date"],
        end_date=row["end_date"],
        start_ts=row.get("start_ts"),
        end_ts=row.get("end_ts"),
        status="announced",
    )


# Curated routes: legs for closures whose notice names no usable end (data/closure_routes.csv).
#
#     closure_id    the event_closures row the legs replace (required)
#     leg           1, 2, 3 ... in course order (required)
#     street, from_street, to_street   one from-to chain per leg (required)
#     note          who supplied the course and from what; shown on the event (required, leg 1)

ROUTE_COLUMNS = ("closure_id", "leg", "street", "from_street", "to_street", "note")


def load_routes(path: Path) -> tuple[dict[str, dict], list[RowError]]:
    """{closure_id: {"legs": [...], "note": str}}, legs in order. A closure with a bad leg is
    dropped whole and reported, so a half route never reaches the map."""
    if not path.exists():
        return {}, []
    errors: list[RowError] = []
    raw: dict[str, list[tuple[int, int, dict]]] = {}
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        absent = [k for k in ROUTE_COLUMNS if k not in (reader.fieldnames or [])]
        if absent:
            return {}, [RowError(path.name, 1, f"header missing columns {', '.join(absent)}")]
        for p in reader:
            p = {k: (v or "").strip() for k, v in p.items() if k is not None}
            if not any(p.values()):
                continue
            raw.setdefault(p["closure_id"], []).append((reader.line_num, p))
    routes: dict[str, dict] = {}
    for cid, rows in raw.items():
        bad = []
        for line, p in rows:
            missing = [k for k in ROUTE_COLUMNS[:5] if not p.get(k)]
            if missing:
                bad.append(RowError(path.name, line, f"missing {', '.join(missing)}"))
            elif not p["leg"].isdigit():
                bad.append(RowError(path.name, line, f"leg {p['leg']!r} is not a number"))
        if not bad:
            nums = sorted(int(p["leg"]) for _, p in rows)
            if nums != list(range(1, len(rows) + 1)):
                bad.append(RowError(path.name, rows[0][0], f"{cid}: legs {nums} are not 1..{len(rows)}"))
        note = next((p["note"] for _, p in rows if p.get("note")), "")
        if not bad and not note:
            bad.append(RowError(path.name, rows[0][0], f"{cid}: no note saying where the course came from"))
        if bad:
            errors += bad
            continue
        legs = [p for _, p in sorted(rows, key=lambda lp: int(lp[1]["leg"]))]
        routes[cid] = {"legs": legs, "note": note}
    return routes, errors
