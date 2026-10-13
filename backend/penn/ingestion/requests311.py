"""Philly311 requests via the City's Carto SQL API (table public_cases_fc, since Dec 2014).

Confirm the column list in recon with:
    SELECT * FROM public_cases_fc LIMIT 1
The SELECT below assumes service_request_id, service_name, status, requested_datetime,
closed_datetime, address, lat, lon.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterator

import httpx

from ..config import CARTO_SQL_URL, CENTER_CITY_BBOX
from ..geo.h3util import point_cell
from ..models import NormalizedEvent

SOURCE = "philly_311"
DATASET_PAGE = "https://opendataphilly.org/datasets/311-service-and-information-requests/"


def build_query(start: str, end: str, bbox: dict = CENTER_CITY_BBOX) -> str:
    return (
        "SELECT service_request_id, service_name, status, requested_datetime, "
        "closed_datetime, address, lat, lon FROM public_cases_fc "
        f"WHERE requested_datetime >= '{start}' AND requested_datetime < '{end}' "
        "AND lat IS NOT NULL AND lon IS NOT NULL"
    )


def fetch_year(year: int, client: httpx.Client | None = None) -> Iterator[dict]:
    own = client is None
    client = client or httpx.Client(timeout=120)
    try:
        for month in range(1, 13):
            start = f"{year}-{month:02d}-01"
            end = f"{year + 1}-01-01" if month == 12 else f"{year}-{month + 1:02d}-01"
            r = client.get(CARTO_SQL_URL, params={"q": build_query(start, end), "format": "json"})
            r.raise_for_status()
            rows = r.json().get("rows", [])
            print(f" {start[:7]}: {len(rows)} rows")
            yield from rows
    finally:
        if own:
            client.close()


def _ts(v: str | None) -> datetime | None:
    if not v:
        return None
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize(row: dict, category: str = "") -> NormalizedEvent | None:
    if row.get("lat") is None or row.get("lon") is None:
        return None
    lat, lon = float(row["lat"]), float(row["lon"])
    return NormalizedEvent(
        source=SOURCE,
        source_record_id=str(row["service_request_id"]),
        event_type=category or "reported_issue",
        title=row.get("service_name") or "311 request",
        description=row.get("address") or "",
        source_url=DATASET_PAGE,
        source_authority="reported",
        claim_certainty="observed",
        geometry_wkt=f"POINT ({lon:.6f} {lat:.6f})",
        centroid_lat=lat,
        centroid_lon=lon,
        h3_r9=point_cell(lon, lat),
        h3_cells=[point_cell(lon, lat)],
        start_ts=_ts(row.get("requested_datetime")),
        end_ts=_ts(row.get("closed_datetime")),
        status=row.get("status") or "",
        raw_status=row.get("status") or "",
    )
