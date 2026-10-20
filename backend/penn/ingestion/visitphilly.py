"""p3-13: visitphilly's weekly "Things to Do This Week & Weekend" picks, as event rows.

Input: data/snapshots/visitphilly/*.json, written by scripts/fetch_visitphilly.py. Each file is the
whole page as it stood when fetched; files are read oldest first and a later file's version of an item
wins, so mid-week edits replace the earlier text.

What is kept:
- Items whose date line parses (see parse_dates). "Dates vary by event", "Ongoing", "Now open" and
  blank lines are skipped and counted, never guessed.
- Locations inside the city: the point's H3 cell is a city cell, or its ring touches one and the
  "Where:" line names Philadelphia (points on the city line). An item named for another town
  (Camden, NJ; Media, PA) is dropped even if its pin sits near the line.
- An item with several locations becomes one row with a MULTIPOINT of its in-city locations.

Separate dates ("October 13 & 17") become one row each, so a Tuesday game is not "in effect" on the
Thursday between. "Through <date>" starts on the day the snapshot was taken: the page only says the
item is running by then. A qualifier like "(Sundays only)" goes into the description; the row still
spans the whole range.

Rows are source_authority="curated" (an editorial pick, not a City record). source_url links back to
visitphilly, as its terms of use require: the item's own visitphilly article when it has one, else its
heading on the weekly page (#<textid>), never the organizer's site. organization names visitphilly as
the lister.
description is the "Where:" line, plus a qualifier like "Sundays only".
"""
from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path

from ..config import LOCAL_TZ
from ..geo.h3util import point_cell, ring
from ..geo.locate import city_cells
from ..models import NormalizedEvent

SOURCE = "visitphilly"
PAGE_URL = "https://www.visitphilly.com/uwishunu/things-to-do-in-philadelphia-this-week-weekend/"

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTH = r"(January|February|March|April|May|June|July|August|September|October|November|December)"
WEEKDAY = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
DASH = r"\s*[-–—]\s*"


@dataclass(frozen=True)
class Span:
    start: date
    end: date
    start_time: time | None = None
    end_time: time | None = None


class Unparsed(ValueError):
    """The date line says nothing PENN can place on a calendar."""


def _d(month: str, day: str, year: str) -> date:
    return date(int(year), MONTHS[month.lower()], int(day))


_CLOCK = re.compile(r"(noon|midnight|\d{1,2}(?::\d{2})?)\s*(a\.?m\.?|p\.?m\.?)?", re.I)


def _clock(s: str, meridiem: str | None) -> time:
    s = s.lower()
    if s == "noon":
        return time(12, 0)
    if s == "midnight":
        return time(0, 0)
    h, _, m = s.partition(":")
    hour, minute = int(h), int(m or 0)
    pm = (meridiem or "").lower().startswith("p")
    if pm and hour != 12:
        hour += 12
    if not pm and meridiem and hour == 12:
        hour = 0
    return time(hour, minute)


def parse_times(s: str) -> tuple[time | None, time | None]:
    """'7 a.m.', '10 a.m. - 4 p.m.', 'Noon-8 p.m.', '7:30-11 p.m.' (start takes the end's a.m./p.m.)."""
    parts = re.split(DASH, s.strip(), maxsplit=1)
    found = [_CLOCK.fullmatch(p.strip()) for p in parts]
    if not all(found):
        return None, None
    if len(found) == 1:
        return _clock(found[0].group(1), found[0].group(2)), None
    (a, am), (b, bm) = found[0].groups(), found[1].groups()
    start = _clock(a, am or (bm if a.lower() not in ("noon", "midnight") else None))
    end = _clock(b, bm)
    if not am and bm and start > end and start.hour >= 12:   # '11-1 p.m.': the start is a.m.
        start = time(start.hour - 12, start.minute)
    return start, end


_SINGLE = re.compile(rf"^{WEEKDAY}?{MONTH}\s+(\d{{1,2}}),\s*(\d{{4}})(?:\s*\|\s*(.+))?$", re.I)
_SAME_MONTH = re.compile(rf"^{MONTH}\s+(\d{{1,2}}){DASH}(\d{{1,2}}),\s*(\d{{4}})$", re.I)
_TWO_MONTHS = re.compile(rf"^{MONTH}\s+(\d{{1,2}}){DASH}{MONTH}\s+(\d{{1,2}}),\s*(\d{{4}})$", re.I)
_TWO_YEARS = re.compile(rf"^{MONTH}\s+(\d{{1,2}}),\s*(\d{{4}}){DASH}{MONTH}\s+(\d{{1,2}}),\s*(\d{{4}})$", re.I)
_TO_MONTH = re.compile(rf"^{MONTH}\s+(\d{{1,2}}),\s*(\d{{4}}){DASH}{MONTH}\s+(\d{{4}})$", re.I)
_LIST = re.compile(rf"^{MONTH}\s+((?:\d{{1,2}}\s*(?:,|&|and)\s*)+\d{{1,2}}),\s*(\d{{4}})$", re.I)
_THROUGH = re.compile(rf"^Through\s+{WEEKDAY}?{MONTH}\s+(\d{{1,2}}),\s*(\d{{4}})\s*(?:\((.+)\))?$", re.I)


def parse_dates(text: str, seen_on: date) -> tuple[list[Span], str]:
    """(spans, qualifier) for one item's date line. seen_on is the snapshot's date, the start of a
    'Through ...' item. Raises Unparsed for lines with no usable dates."""
    t = re.sub(r"\s+", " ", text).strip()
    if m := _SINGLE.match(t):
        d = _d(*m.groups()[:3])
        st, et = parse_times(m.group(4)) if m.group(4) else (None, None)
        return [Span(d, d, st, et)], ""
    if m := _SAME_MONTH.match(t):
        mon, a, b, y = m.groups()
        return [Span(_d(mon, a, y), _d(mon, b, y))], ""
    if m := _TWO_MONTHS.match(t):
        m1, a, m2, b, y = m.groups()
        return [Span(_d(m1, a, y), _d(m2, b, y))], ""
    if m := _TWO_YEARS.match(t):
        m1, a, y1, m2, b, y2 = m.groups()
        return [Span(_d(m1, a, y1), _d(m2, b, y2))], ""
    if m := _TO_MONTH.match(t):
        m1, a, y1, m2, y2 = m.groups()
        last = calendar.monthrange(int(y2), MONTHS[m2.lower()])[1]
        return [Span(_d(m1, a, y1), date(int(y2), MONTHS[m2.lower()], last))], ""
    if m := _LIST.match(t):
        mon, days, y = m.groups()
        return [Span(_d(mon, d, y), _d(mon, d, y)) for d in re.findall(r"\d{1,2}", days)], ""
    if m := _THROUGH.match(t):
        mon, d, y, qualifier = m.groups()
        end = _d(mon, d, y)
        return [Span(min(seen_on, end), end)], qualifier or ""
    raise Unparsed(t or "(no date line)")


def _town(where: str) -> str:
    """'Xfinity Mobile Arena, 3601 S. Broad Street, Philadelphia, PA' -> 'Philadelphia'."""
    m = re.search(r",\s*([^,]+),\s*(?:PA|NJ|DE)\s*$", where.strip())
    return m.group(1).strip() if m else ""


def in_city(lon: float, lat: float, where: str = "") -> bool:
    city = city_cells()
    cell = point_cell(lon, lat)
    town = _town(where)
    if town and town.lower() != "philadelphia":
        return False
    if cell in city:
        return True
    # A pin on the city line can sit in a cell whose centre is outside; trust it only when the
    # address says Philadelphia.
    return town.lower() == "philadelphia" and bool(city.intersection(ring(cell, 1)))


def _points(item: dict) -> list[tuple[float, float]]:
    pts = []
    loc = item.get("location")
    if loc and in_city(loc["lng"], loc["lat"], item.get("where", "")):
        pts.append((loc["lng"], loc["lat"]))
    for o in item.get("other_locations") or []:
        if o.get("lat") is None or o.get("lng") is None:
            continue
        town = (o.get("city") or "").split(",")[0].strip()   # usually blank; sometimes "Glenside"
        where = f"{o.get('address', '')}, {town}, PA" if town else ""
        if in_city(o["lng"], o["lat"], where) and (o["lng"], o["lat"]) not in pts:
            pts.append((o["lng"], o["lat"]))
    return pts


def _wkt(pts: list[tuple[float, float]]) -> str:
    if len(pts) == 1:
        return f"POINT ({pts[0][0]:.6f} {pts[0][1]:.6f})"
    return "MULTIPOINT (" + ", ".join(f"({x:.6f} {y:.6f})" for x, y in pts) + ")"


def _ts(d: date, t: time | None) -> datetime | None:
    return datetime.combine(d, t, LOCAL_TZ) if t else None


def _source_url(item: dict) -> str:
    """The item's own visitphilly.com article when it has one; else its heading on the weekly page.

    The weekly page's own script decides where it scrolls: it rewrites any fragment to #<textid> and
    animates down from the top, which can take many seconds or stall. Neither the section's real id
    (#vp-article-section--item-N) nor a text fragment (#:~:text=) gets past it (tested Oct 17), so an
    article, a page about this one item, is the only link that reliably lands on it. The weekly page is
    also rewritten every Monday, so an old week's anchor lands on the top of the current page."""
    for url in (item.get("link"), item.get("read_more")):
        if url and re.match(r"https?://(www\.)?visitphilly\.com/", url) and not url.startswith(PAGE_URL):
            return url
    return f"{PAGE_URL}#{item['textid']}" if item.get("textid") else PAGE_URL


def to_events(item: dict, seen_on: date, last_updated: str | None) -> list[NormalizedEvent]:
    """Raises Unparsed when the date line has no usable dates; returns [] when no location is in the city."""
    spans, qualifier = parse_dates(item.get("date_text", ""), seen_on)
    pts = _points(item)
    if not pts:
        return []
    cells = sorted({point_cell(x, y) for x, y in pts})
    lon, lat = pts[0]
    # description is the place, as permits use it ("; " between parts); the date lives in the date fields.
    desc = "; ".join(x for x in (item.get("where", ""), qualifier.capitalize() if qualifier else "") if x)
    out = []
    for k, s in enumerate(spans, 1):
        rid = f"{item['textid']}-{s.start.year}" + (f"-{k}" if len(spans) > 1 else "")
        out.append(NormalizedEvent(
            source=SOURCE,
            source_record_id=rid,
            event_type="event",
            title=item["title"],
            description=desc,
            source_url=_source_url(item),
            source_authority="curated",
            claim_certainty="scheduled",
            geometry_wkt=_wkt(pts),
            centroid_lat=lat,
            centroid_lon=lon,
            h3_r9=point_cell(lon, lat),
            h3_cells=cells,
            organization=f"visitphilly.com, updated {last_updated}" if last_updated else "visitphilly.com",
            occupancy="venue",
            start_date=s.start,
            end_date=s.end,
            start_ts=_ts(s.start, s.start_time),
            end_ts=_ts(s.start, s.end_time) if s.start == s.end else None,
            status="listed",
        ))
    return out


@dataclass
class LoadReport:
    files: int = 0
    items: int = 0
    events: int = 0
    unparsed: list[str] = field(default_factory=list)
    outside: list[str] = field(default_factory=list)


def load_all(folder: Path) -> tuple[list[NormalizedEvent], LoadReport, list[tuple[str, dict]]]:
    """(events, report, raw items) from every snapshot, oldest first; a later file's item wins."""
    latest: dict[str, tuple[dict, date, str | None]] = {}
    files = sorted(folder.glob("*.json"))
    for path in files:
        snap = json.loads(path.read_text(encoding="utf-8"))
        seen_on = datetime.fromisoformat(snap["fetched_at"]).astimezone(LOCAL_TZ).date()
        for item in snap["items"]:
            latest[item["textid"]] = (item, seen_on, snap.get("last_updated"))
    rep = LoadReport(files=len(files), items=len(latest))
    events: dict[str, NormalizedEvent] = {}
    for item, seen_on, updated in latest.values():
        try:
            evs = to_events(item, seen_on, updated)
        except Unparsed as exc:
            rep.unparsed.append(f"{item['title']}: {exc}")
            continue
        if not evs:
            rep.outside.append(f"{item['title']}: {item.get('where') or 'no location'}")
        for e in evs:
            events[e.source_record_id] = e
    rep.events = len(events)
    raw = [(textid, item) for textid, (item, _, _) in latest.items()]
    return list(events.values()), rep, raw
