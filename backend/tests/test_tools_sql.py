"""Runs the real schema and tool SQL on embedded ClickHouse (chdb). Skipped if chdb is missing.
Install with: pip install -e ".[sqltest]" (Linux and macOS only; skipped on Windows)."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

chdb_session = pytest.importorskip("chdb.session")
from clickhouse_connect.driver.binding import finalize_query  # noqa: E402

from penn import db, tools  # noqa: E402
from penn.ingestion.lane_closures import group_by_permit  # noqa: E402

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
    row = permit.to_row()
    row["updated_at"] = datetime.now(timezone.utc)
    s.query(f"INSERT INTO penn.events ({','.join(db.EVENT_COLUMNS)}) VALUES "
            f"({','.join(_lit(row[c]) for c in db.EVENT_COLUMNS)})")
    original = db.query
    db.query = q
    yield permit
    db.query = original


def test_permit_window_edges(fixture_db):
    permit = fixture_db
    cells = permit.h3_cells
    assert tools.search_events(cells, "2026-10-07T00:00:00-04:00", "2026-10-08T00:00:00-04:00")["count"] == 0
    assert tools.search_events(cells, "2026-10-08T00:00:00-04:00", "2026-10-09T00:00:00-04:00")["count"] == 1
    assert tools.search_events(cells, "2026-10-21T00:00:00-04:00", "2026-10-22T00:00:00-04:00")["count"] == 0
