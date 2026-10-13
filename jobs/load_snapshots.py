"""Rebuild lane-closure events from the saved daily snapshots, oldest first.

    python jobs/load_snapshots.py

Each file is grouped and inserted in date order, so the newest version of a permit
wins (ReplacingMergeTree keeps the latest updated_at) and permits that later left
the layer keep their last-seen version. Run it after changing the type mapping.

A snapshot with under half the previous file's rows is treated as truncated and
skipped (2026-10-10 stopped after one 2,000-row page).
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from penn import db  # noqa: E402
from penn.ingestion import lane_closures  # noqa: E402

SNAPSHOT_DIR = ROOT / "data" / "snapshots"
MIN_SHARE = 0.5


def main() -> None:
    prev_rows = 0
    for path in sorted(SNAPSHOT_DIR.glob("*.geojson.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            feats = json.load(f)["features"]
        if prev_rows and len(feats) < MIN_SHARE * prev_rows:
            print(f"{path.name}: {len(feats)} rows vs {prev_rows} before, skipped as truncated")
            continue
        n = db.insert_events(lane_closures.group_by_permit(feats))
        print(f"{path.name}: {len(feats)} rows -> {n} events")
        prev_rows = len(feats)


if __name__ == "__main__":
    main()
