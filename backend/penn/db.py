"""Thin ClickHouse access layer."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

import clickhouse_connect

from .config import settings
from .models import NormalizedEvent

EVENT_COLUMNS = [
    "event_id", "source", "source_record_id", "event_type", "title", "description",
    "organization", "occupancy", "source_url", "source_authority", "claim_certainty",
    "geometry_wkt", "centroid_lat", "centroid_lon", "h3_r9", "h3_cells", "seg_ids",
    "start_date", "end_date", "start_ts", "end_ts", "open_ended", "status", "raw_status",
    "updated_at",
]


@lru_cache(maxsize=1)
def client():
    return clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
        database=settings.clickhouse_db,
        secure=settings.clickhouse_secure,
    )


def insert_events(events: list[NormalizedEvent]) -> int:
    if not events:
        return 0
    now = datetime.now(timezone.utc)
    rows = []
    for e in events:
        r = e.to_row()
        r["updated_at"] = now
        rows.append([r[c] for c in EVENT_COLUMNS])
    client().insert("events", rows, column_names=EVENT_COLUMNS)
    return len(rows)


def insert_snapshots(rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    client().insert("permit_snapshots", [[r[c] for c in cols] for r in rows], column_names=cols)
    return len(rows)


def insert_closures(rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    client().insert("event_closures", [[r[c] for c in cols] for r in rows], column_names=cols)
    return len(rows)


def query(sql: str, params: dict | None = None) -> list[dict]:
    res = client().query(sql, parameters=params or {})
    return [dict(zip(res.column_names, row)) for row in res.result_rows]
