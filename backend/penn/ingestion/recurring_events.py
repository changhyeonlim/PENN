"""One-time curated backfill of recurring events (marathon, Broad Street Run, ...).

Input: data/recurring_events/*.geojson, one FeatureCollection per event series.
Each feature is ONE edition (one year) of the event. Required properties:

    series        stable slug, e.g. "philadelphia-marathon"
    name          display name of that edition
    year          int
    date          "YYYY-MM-DD" (local first day of the event)
    end_date      "YYYY-MM-DD" (optional; last day, for multi-day events)
    closure_start "HH:MM" local, from the official closure notice (optional)
    closure_end   "HH:MM" local (optional; applies to `date` only)
    kind          "route" | "area" | "venue" (optional; inferred from the geometry)
    source_url    the official page the route and times came from (required)

Geometry, in WGS84:
    route  LineString or MultiLineString (races, parades)
    area   Polygon or MultiPolygon (festivals that fill streets, e.g. South St)
    venue  Point (stadiums, arenas; one location, nearby closures unknown)

Rows are marked source_authority="curated": the team assembled them by hand from
official pages. Never add an edition without a source_url.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time
from pathlib import Path

from ..config import LOCAL_TZ
from ..geo.h3util import centroid, line_cells, multiline_wkt, point_cell, polygon_cells
from ..models import NormalizedEvent

SOURCE = "recurring_events"
REQUIRED = ("series", "name", "year", "date", "source_url")


class CurationError(ValueError):
    pass


def _shape(geom: dict) -> tuple[str, str, float, float, list[int]]:
    """Return (kind, wkt, lat, lon, cells) for a route, area or venue geometry."""
    t = geom["type"]
    if t in ("LineString", "MultiLineString"):
        lines = [[tuple(c) for c in geom["coordinates"]]] if t == "LineString" else \
            [[tuple(c) for c in ln] for ln in geom["coordinates"]]
        lon, lat = centroid([pt for ln in lines for pt in ln])
        cells: list[int] = []
        for ln in lines:
            for c in line_cells(ln):
                if c not in cells:
                    cells.append(c)
        return "route", multiline_wkt(lines), lat, lon, cells
    if t in ("Polygon", "MultiPolygon"):
        polys = [geom["coordinates"]] if t == "Polygon" else geom["coordinates"]
        lon, lat = centroid([tuple(p) for poly in polys for p in poly[0]])
        cells = sorted({c for poly in polys for c in polygon_cells(poly)})
        # A narrow area (one street) can hold no cell centres; use the cells its outline crosses.
        outline = {c for poly in polys for c in line_cells([tuple(p) for p in poly[0]])}
        cells = sorted(set(cells) | outline)
        rings = ", ".join("((" + ", ".join(f"{x:.6f} {y:.6f}" for x, y in poly[0]) + "))" for poly in polys)
        return "area", f"MULTIPOLYGON ({rings})", lat, lon, cells
    if t == "Point":
        lon, lat = geom["coordinates"][:2]
        return "venue", f"POINT ({lon:.6f} {lat:.6f})", lat, lon, [point_cell(lon, lat)]
    raise CurationError(f"unsupported geometry {t}")


def load_file(path: Path) -> list[NormalizedEvent]:
    fc = json.loads(path.read_text())
    out = []
    for f in fc.get("features", []):
        p = f.get("properties", {})
        missing = [k for k in REQUIRED if not p.get(k)]
        if missing:
            raise CurationError(f"{path.name}: edition missing {missing}")
        if not f.get("geometry"):
            raise CurationError(f"{path.name}: {p['name']} {p['year']} has no geometry")
        kind, wkt, lat, lon, cells = _shape(f["geometry"])
        d = date.fromisoformat(p["date"])
        d_end = date.fromisoformat(p["end_date"]) if p.get("end_date") else d
        if d_end < d:
            raise CurationError(f"{path.name}: {p['name']} ends before it starts")
        start_ts = end_ts = None
        if p.get("closure_start") and p.get("closure_end"):
            start_ts = datetime.combine(d, time.fromisoformat(p["closure_start"]), LOCAL_TZ)
            end_ts = datetime.combine(d, time.fromisoformat(p["closure_end"]), LOCAL_TZ)
        past = d_end < date.today()
        out.append(
            NormalizedEvent(
                source=SOURCE,
                source_record_id=f"{p['series']}-{p['year']}",
                event_type="event",
                title=p["name"],
                description=p.get("notes", ""),
                source_url=p["source_url"],
                source_authority="curated",
                claim_certainty="observed" if past else "scheduled",
                geometry_wkt=wkt,
                centroid_lat=lat,
                centroid_lon=lon,
                h3_r9=point_cell(lon, lat),
                h3_cells=cells,
                organization=p.get("organizer", ""),
                occupancy=p.get("kind", kind),
                start_date=d,
                end_date=d_end,
                start_ts=start_ts,
                end_ts=end_ts,
                status="held" if past else "scheduled",
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
