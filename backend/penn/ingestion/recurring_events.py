"""One-time curated backfill of recurring events (marathon, Broad Street Run, ...).

Input: data/recurring_events/*.geojson, one FeatureCollection per event series.
Each feature is ONE edition (one year) of the event. Required properties:

    series        stable slug, e.g. "philadelphia-marathon"
    name          display name of that edition
    year          int
    date          "YYYY-MM-DD" (local date of the event)
    closure_start "HH:MM" local, from the official closure notice (optional)
    closure_end   "HH:MM" local (optional)
    source_url    the official page the route and times came from (required)

Geometry: LineString or MultiLineString of the route, in WGS84.

Rows are marked source_authority="curated": the team assembled them by hand from
official pages. Never add an edition without a source_url.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

from ..config import LOCAL_TZ
from ..geo.h3util import centroid, line_cells, multiline_wkt, point_cell
from ..models import NormalizedEvent

SOURCE = "recurring_events"
REQUIRED = ("series", "name", "year", "date", "source_url")


class CurationError(ValueError):
    pass


def _lines(geom: dict) -> list[list[tuple[float, float]]]:
    if geom["type"] == "LineString":
        return [[tuple(c) for c in geom["coordinates"]]]
    if geom["type"] == "MultiLineString":
        return [[tuple(c) for c in ln] for ln in geom["coordinates"]]
    raise CurationError(f"route must be a line, got {geom['type']}")


def load_file(path: Path) -> list[NormalizedEvent]:
    fc = json.loads(path.read_text())
    out = []
    for f in fc.get("features", []):
        p = f.get("properties", {})
        missing = [k for k in REQUIRED if not p.get(k)]
        if missing:
            raise CurationError(f"{path.name}: edition missing {missing}")
        if not f.get("geometry"):
            raise CurationError(f"{path.name}: {p['name']} {p['year']} has no route geometry")
        lines = _lines(f["geometry"])
        pts = [pt for ln in lines for pt in ln]
        lon, lat = centroid(pts)
        cells: list[int] = []
        for ln in lines:
            for c in line_cells(ln):
                if c not in cells:
                    cells.append(c)
        d = date.fromisoformat(p["date"])
        start_ts = end_ts = None
        if p.get("closure_start") and p.get("closure_end"):
            start_ts = datetime.combine(d, time.fromisoformat(p["closure_start"]), LOCAL_TZ)
            end_ts = datetime.combine(d, time.fromisoformat(p["closure_end"]), LOCAL_TZ)
        out.append(
            NormalizedEvent(
                source=SOURCE,
                source_record_id=f"{p['series']}-{p['year']}",
                event_type="event",
                title=p["name"],
                description=p.get("notes", ""),
                source_url=p["source_url"],
                source_authority="curated",
                claim_certainty="observed" if d < date.today() else "scheduled",
                geometry_wkt=multiline_wkt(lines),
                centroid_lat=lat,
                centroid_lon=lon,
                h3_r9=point_cell(lon, lat),
                h3_cells=cells,
                organization=p.get("organizer", ""),
                start_date=d,
                end_date=d,
                start_ts=start_ts,
                end_ts=end_ts,
                status="held" if d < date.today() else "scheduled",
            )
        )
    return out


def load_all(folder: Path) -> list[NormalizedEvent]:
    events = []
    for path in sorted(folder.glob("*.geojson")):
        events.extend(load_file(path))
    return events


def recurrence(events: list[NormalizedEvent], cells: set[int]) -> dict[str, dict]:
    """For each series, how many editions touched any of the given cells."""
    by_series: dict[str, dict] = {}
    for e in events:
        series = e.source_record_id.rsplit("-", 1)[0]
        s = by_series.setdefault(series, {"editions": 0, "touched": 0, "years": []})
        s["editions"] += 1
        if cells.intersection(e.h3_cells):
            s["touched"] += 1
            s["years"].append(e.start_date.year if e.start_date else None)
    return by_series
