"""Freeze an event's closures from ClickHouse into eval/records/event_closures_<series>.json.

    python eval/freeze_closures.py thanksgiving-day-parade philadelphia-marathon

Street-history questions cite these IDs; validate_questions.py checks them against these files,
so expectations never depend on whatever the live database holds later.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from penn import db  # noqa: E402
from penn.config import LOCAL_TZ  # noqa: E402

RECORDS = Path(__file__).parent / "records"


def _local(ts):
    return ts.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).isoformat() if ts else None


def freeze(series: str) -> Path:
    q = f"SELECT * FROM penn.event_closures FINAL WHERE series = '{series}' ORDER BY start_date, closure_id"
    rows = db.query(q)
    for r in rows:
        r["start_ts_local"], r["end_ts_local"] = _local(r["start_ts"]), _local(r["end_ts"])
        for k, v in list(r.items()):
            if not isinstance(v, (str, int, float, bool, type(None))):
                r[k] = str(v)
    out = RECORDS / f"event_closures_{series}.json"
    out.write_text(json.dumps({"table": "penn.event_closures", "series": series,
                               "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "source_query": q, "rows": rows}, indent=1), encoding="utf-8")
    print(f"{out.name}: {len(rows)} rows")
    return out


if __name__ == "__main__":
    for s in sys.argv[1:]:
        freeze(s)
