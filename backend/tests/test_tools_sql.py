"""Runs the real schema and tool SQL on embedded ClickHouse (chdb). Skipped if chdb is missing.
Install with: pip install chdb"""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

chdb_session = pytest.importorskip("chdb.session")
from clickhouse_connect.driver.binding import finalize_query  # noqa: E402

from penn import db, tools  # noqa: E402
from penn.ingestion.lane_closures import group_by_permit  # noqa: E402
from penn.ingestion.requests311 import normalize  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema.sql"
OCT_8, OCT_20 = 1791417600000, 1792454400000  # UTC-midnight dates as the layer stores them


def _lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "[" + ",".join(map(str, v)) + "]"
    if isinstance(v, datetime):
        return f"'{v.astimezone(timezone.utc):%Y-%m-%d %H:%M:%S}'"
    if isinstance(v, date):
        return f"'{v}'"
    return "'" + str(v).replace("'", "\\'") + "'"


@pytest.fixture(scope="module")
def fixture_db():
    s = chdb_session.Session()
    for stmt in SCHEMA.read_text().split(";"):
        if "CREATE" in stmt:
            s.query(stmt)

    def q(sql, params=None):
        out = s.query(finalize_query(sql, params or {}), "JSONEachRow")
        return [json.loads(line) for line in str(out).splitlines() if line.strip()]

    permit = group_by_permit([{"geometry": {"type": "LineString", "coordinates": [[-75.1660, 39.9497], [-75.1645, 39.9499]]},
                               "properties": {"objectid": 1, "permitnumber": "2025-99999", "permittype": "Utility Work Excavation",
                                              "occupancytype": "Full Closure", "purpose": "WATER MAIN", "status": "Current",
                                              "address": "1500 block of WALNUT ST", "effectivedate": OCT_8, "expirationdate": OCT_20}}])[0]
    report = normalize({"service_request_id": 123, "service_name": "Street Defect", "status": "Open",
                        "requested_datetime": "2026-09-26T13:30:00Z", "lat": 39.9498, "lon": -75.1652})
    report.event_type = "street_condition"
    for e in (permit, report):
        row = e.to_row()
        row["updated_at"] = datetime.now(timezone.utc)
        s.query(f"INSERT INTO penn.events ({','.join(db.EVENT_COLUMNS)}) VALUES "
                f"({','.join(_lit(row[c]) for c in db.EVENT_COLUMNS)})")
    original = db.query
    db.query = q
    yield permit, report
    db.query = original


def test_permit_window_edges(fixture_db):
    permit, _ = fixture_db
    cells = permit.h3_cells
    assert tools.search_events(cells, "2026-10-07T00:00:00-04:00", "2026-10-08T00:00:00-04:00")["count"] == 0
    assert tools.search_events(cells, "2026-10-08T00:00:00-04:00", "2026-10-09T00:00:00-04:00")["count"] == 1
    assert tools.search_events(cells, "2026-10-21T00:00:00-04:00", "2026-10-22T00:00:00-04:00")["count"] == 0


def test_analyze_groups_by_occupancy(fixture_db):
    permit, _ = fixture_db
    r = tools.analyze_disruption(permit.h3_cells, "2026-10-10T06:00:00-04:00", "2026-10-10T12:00:00-04:00")
    assert r["by_occupancy"]["Full Closure"] == ["lane_closures:2025-99999"]


def test_timeline_and_outlook(fixture_db):
    _, report = fixture_db
    tl = tools.get_location_timeline(report.h3_cells, "2026-01-01T00:00:00-05:00", "2026-10-05T00:00:00-04:00")
    assert tl["monthly_311"][0]["n"] == 1 and tl["closure_history_starts"] is None
    o = tools.disruption_outlook(report.h3_cells, "2026-10-03T09:00:00-04:00", "2026-10-03T12:00:00-04:00")
    assert o["target_hits"] == 1 and o["past_windows"] == 156 and o["enough_history"]
