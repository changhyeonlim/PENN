"""Street Lane Closures (City of Philadelphia, ArcGIS FeatureServer).

Facts confirmed during recon (Oct 2026):
- Each feature is ONE street segment (seg_id). One permit number can span many rows,
  so rows are grouped by permit number into one event.
- Dates (effectivedate, expirationdate, raindate) are date-only values at UTC midnight.
- status values seen: Current, Future. Most expired permits are dropped, which is
  why `snapshot_rows` must run daily from day one. But not all: the Oct 13, 2026
  snapshot still lists ~850 permits past their expiration date, so filter on dates,
  never on presence in the layer.
- Errors come back as HTTP 200 with an "error" body; `fetch_features` raises on them.
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

# Every permittype seen in the layer (snapshots Oct 4-13, 2026) -> PENN event_type.
# A type not listed here becomes "other", never silently "roadwork".
PERMIT_TYPES = {
    "Utility Work Excavation": "utility_work",
    "Utility Work Non-Excavation": "utility_work",
    "PWD_CW": "utility_work",                     # Water Department excavation
    "PLUMBER": "utility_work",                    # service-line excavation
    "PGW EUN Excavation": "utility_work",         # PGW emergency utility notice
    "PGW EUN No Excavation": "utility_work",
    "Municipal Contract Work": "roadwork",
    "Milling/Resurfacing": "roadwork",
    "Milling/Resurfacing - Planning Only": "roadwork",
    "Equipment Placement": "construction",        # includes sidewalk sheds (Footway)
    "Crane Placement": "construction",
    "Block Party": "block_party",
    "Temporary No Parking": "parking_restriction",
    "Temporary Loading Zone": "parking_restriction",
    "Temporary Passenger Loading Zone": "parking_restriction",
    "Banner Installation": "other",
}
PARKING_TYPES = {t for t, k in PERMIT_TYPES.items() if k == "parking_restriction"}
# Planning-only permits are a resurfacing plan with no expiry, not scheduled work.
PLANNING_ONLY_SUFFIX = "Planning Only"


class FetchError(RuntimeError):
    """The layer returned an error or fewer rows than it says it has."""


def _get_json(client: httpx.Client, params: dict) -> dict:
    r = client.get(LANE_CLOSURES_URL, params=params)
    r.raise_for_status()
    body = r.json()
    # ArcGIS reports errors with HTTP 200 and an "error" body; treat that as a failure, not an empty page.
    if "error" in body:
        raise FetchError(f"ArcGIS error: {body['error']}")
    return body


def fetch_features(client: httpx.Client | None = None) -> Iterator[dict]:
    """Page through every feature as GeoJSON in WGS84.

    Raises FetchError if any page errors, or if the total does not match the layer's own count.
    """
    own = client is None
    client = client or httpx.Client(timeout=60)
    try:
        expected = _get_json(client, {"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]
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
            body = _get_json(client, params)
            feats = body.get("features", [])
            yield from feats
            offset += len(feats)
            more = (body.get("properties") or {}).get("exceededTransferLimit", False)
            if not feats or (not more and len(feats) < LANE_CLOSURES_PAGE):
                break
        if offset != expected:
            raise FetchError(f"got {offset} features, layer reports {expected}")
    finally:
        if own:
            client.close()


def _event_type(permit_type: str) -> str:
    return PERMIT_TYPES.get(permit_type.strip(), "other")


def _is_planning_only(permit_type: str) -> bool:
    return permit_type.strip().endswith(PLANNING_ONLY_SUFFIX)


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
        planning = _is_planning_only(permit_type)
        events.append(
            NormalizedEvent(
                source=SOURCE,
                source_record_id=permit,
                event_type=_event_type(permit_type),
                title=(p0.get("purpose") or permit_type or "Permit").strip().title(),
                description="; ".join(addresses),
                source_url=DATASET_PAGE,
                source_authority="official",
                claim_certainty="planned" if planning else "scheduled",
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
                status="planning_only" if planning else "permitted",
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
