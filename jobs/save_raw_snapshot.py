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


def save_snapshot(snapshot_date: date) -> Path:
    features = list(lane_closures.fetch_features())
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
