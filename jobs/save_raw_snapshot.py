"""Save a raw snapshot of the Lane Closures layer to disk, gzipped GeoJSON.

Unlike jobs/run_job.py (which writes normalized events to ClickHouse), this
script keeps the untouched FeatureServer response -- useful as a durable,
queryable-by-hand backup of exactly what the city published on a given day.

    python jobs/save_raw_snapshot.py                  # writes data/snapshots/<today>.geojson.gz
    python jobs/save_raw_snapshot.py 2026-10-05        # writes a specific date's file

fetch_features() in backend/penn/ingestion/lane_closures.py already pages
through the layer LANE_CLOSURES_PAGE (2000) rows at a time; this script just
drains that generator and serializes everything as one FeatureCollection.
"""
from __future__ import annotations

import gzip
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from penn.config import LOCAL_TZ  # noqa: E402
from penn.ingestion import lane_closures  # noqa: E402

SNAPSHOT_DIR = ROOT / "data" / "snapshots"
# Refuse a snapshot under half the previous day's rows. Daily churn is ~1-2%, so a drop
# this large means a broken fetch or a half-published layer, not real change.
MIN_SHARE = 0.5


class SuspiciousSnapshot(RuntimeError):
    pass


def _previous_rows(snapshot_date: date) -> int | None:
    earlier = [p for p in sorted(SNAPSHOT_DIR.glob("*.geojson.gz")) if p.name < f"{snapshot_date.isoformat()}"]
    if not earlier:
        return None
    with gzip.open(earlier[-1], "rt", encoding="utf-8") as f:
        return len(json.load(f)["features"])


def save_snapshot(snapshot_date: date) -> Path:
    features = list(lane_closures.fetch_features())
    prev = _previous_rows(snapshot_date)
    if prev and len(features) < MIN_SHARE * prev:
        raise SuspiciousSnapshot(f"{len(features)} features vs {prev} in the previous snapshot; not saved")
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = SNAPSHOT_DIR / f"{snapshot_date.isoformat()}.geojson.gz"
    collection = {"type": "FeatureCollection", "features": features}
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(collection, f)
    print(f"{out_path}: wrote {len(features)} features")
    return out_path


def main(argv: list[str]) -> None:
    if len(argv) > 1:
        snapshot_date = datetime.strptime(argv[1], "%Y-%m-%d").date()
    else:
        snapshot_date = datetime.now(LOCAL_TZ).date()
    save_snapshot(snapshot_date)


if __name__ == "__main__":
    main(sys.argv)
