"""Freeze search_events on a corner's exact blocks into eval/records/<slug>_<date>_blocks.json.

    python eval/freeze_corner.py "13th and Locust" 2026-10-10T00:00:00-04:00 2026-10-11T00:00:00-04:00 "Saturday, October 10, 2026"

The file records the corner's cells and seg_ids, so validate_questions.py can check that a
question uses the same place and window as the records its expectations came from.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from penn import tools  # noqa: E402
from penn.geo import locate  # noqa: E402

RECORDS = Path(__file__).parent / "records"


def freeze(corner: str, start: str, end: str, window: str) -> Path:
    loc = locate.from_text(corner)
    if loc.method != "intersection":
        raise SystemExit(f"{corner!r} did not resolve to an intersection ({loc.method})")
    token = tools.LEGS.set(loc.seg_ids)
    try:
        result = tools.search_events(loc.cells, start, end)
    finally:
        tools.LEGS.reset(token)
    slug = re.sub(r"[^a-z0-9]+", "-", corner.lower()).strip("-")
    out = RECORDS / f"{slug}_{start[:10]}_blocks.json"
    out.write_text(json.dumps({
        "place": loc.label, "slug": slug, "cells": loc.cells, "seg_ids": loc.seg_ids,
        "start": start, "end": end, "window": window,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool_call": f"search_events(locate.from_text({corner!r}).cells, {start!r}, {end!r}) with LEGS = its seg_ids",
        "result": result,
    }, indent=1, default=str), encoding="utf-8")
    print(f"{out.name}: {result['count']} records")
    return out


if __name__ == "__main__":
    freeze(*sys.argv[1:5])
