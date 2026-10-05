"""Street Lane Closures (City of Philadelphia, ArcGIS FeatureServer).

Facts confirmed during recon (Oct 2026):
- Each feature is ONE street segment (seg_id). One permit number can span many rows,
  so rows are grouped by permit number into one event.
- Dates (effectivedate, expirationdate, raindate) are date-only values at UTC midnight.
- status values seen: Current, Future. Expired permits appear to be dropped, which is
  why `snapshot_rows` must run daily from day one.
- permittype includes Temporary No Parking and Temporary Loading Zone.
- occupancytype separates Full Closure, Partial Closure, Footway.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterator

import httpx

from ..config import LANE_CLOSURES_PAGE, LANE_CLOSURES_URL
from ..geo.h3util import centroid, line_cells, multiline_wkt, point_cell
from ..models import NormalizedEvent
from ..timeutil import esri_date_to_calendar_date, is_open_ended

SOURCE = "lane_closures"
DATASET_PAGE = "https://opendataphilly.org/datasets/street-lane-closures/"

PARKING_TYPES = {"Temporary No Parking", "Temporary Loading Zone"}


def fetch_features(client: httpx.Client | None = None) -> Iterator[dict]:
    """Page through every feature as GeoJSON in WGS84."""
    own = client is None
    client = client or httpx.Client(timeout=60)
    try:
        offset = 0
        while True:
            params = {
                "where": "1=1",
                "outFields": "*",
                "outSR": 4326,
                "f": "geojson",
                "orderByFields": "objectid",
                "resultOffset": offset,
                "resultRecordCount": LANE_CLOSURES_PAGE,
            }
            r = client.get(LANE_CLOSURES_URL, params=params)
            r.raise_for_status()
            feats = r.json().get("features", [])
            yield from feats
            if len(feats) < LANE_CLOSURES_PAGE:
                break
            offset += LANE_CLOSURES_PAGE
    finally:
        if own:
            client.close()


def _event_type(permit_type: str) -> str:
    if permit_type in PARKING_TYPES:
        return "parking_restriction"
    if "Utility" in permit_type:
        return "utility_work"
    return "roadwork"


def _lines(geom: dict | None) -> list[list[tuple[float, float]]]:
    if not geom:
        return []
    if geom["type"] == "LineString":
        return [[tuple(c) for c in geom["coordinates"]]]
    if geom["type"] == "MultiLineString":
        return [[tuple(c) for c in line] for line in geom["coordinates"]]
    return []


def group_by_permit(features: list[dict]) -> list[NormalizedEvent]:
    """Collapse segment rows into one event per permit number."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for f in features:
        p = f.get("properties", {})
        key = p.get("permitnumber") or f"oid-{p.get('objectid')}"
        groups[key].append(f)

    events = []
    for permit, feats in groups.items():
        p0 = feats[0]["properties"]
        lines = [ln for f in feats for ln in _lines(f.get("geometry"))]
        if not lines:
            continue
        all_pts = [pt for ln in lines for pt in ln]
        lon, lat = centroid(all_pts)
        cells: list[int] = []
        for ln in lines:
            for c in line_cells(ln):
                if c not in cells:
                    cells.append(c)
        start = esri_date_to_calendar_date(p0.get("effectivedate"))
        end = esri_date_to_calendar_date(p0.get("expirationdate"))
        addresses = sorted({f["properties"].get("address") or "" for f in feats} - {""})
        permit_type = p0.get("permittype") or ""
        events.append(
            NormalizedEvent(
                source=SOURCE,
                source_record_id=permit,
                event_type=_event_type(permit_type),
                title=(p0.get("purpose") or permit_type or "Permit").strip().title(),
                description="; ".join(addresses),
                source_url=DATASET_PAGE,
                source_authority="official",
                claim_certainty="scheduled",
                geometry_wkt=multiline_wkt(lines),
                centroid_lat=lat,
                centroid_lon=lon,
                h3_r9=point_cell(lon, lat),
                h3_cells=cells,
                organization="Philadelphia Streets Department (permit issuer)",
                occupancy=p0.get("occupancytype") or "",
                start_date=start,
                end_date=end,
                open_ended=is_open_ended(start, end),
                status="permitted",
                raw_status=p0.get("status") or "",
            )
        )
    return events


def snapshot_rows(features: list[dict], snapshot_date: date) -> list[dict]:
    """One row per permit per day it appears in the layer. This is PENN's closure history."""
    seen: dict[str, dict] = {}
    for f in features:
        p = f.get("properties", {})
        permit = p.get("permitnumber") or f"oid-{p.get('objectid')}"
        if permit in seen:
            seen[permit]["segment_count"] += 1
            continue
        seen[permit] = {
            "snapshot_date": snapshot_date,
            "permit_number": permit,
            "permit_type": p.get("permittype") or "",
            "occupancy": p.get("occupancytype") or "",
            "purpose": p.get("purpose") or "",
            "status": p.get("status") or "",
            "effective_date": esri_date_to_calendar_date(p.get("effectivedate")),
            "expiration_date": esri_date_to_calendar_date(p.get("expirationdate")),
            "segment_count": 1,
        }
    return list(seen.values())
