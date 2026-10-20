"""Re-take every search snapshot in eval/records/ from one database, keeping its cells, window and legs.

    python eval/refreeze_records.py              # from penn_demo, the frozen copy the eval answers from
    python eval/refreeze_records.py --db penn    # from the live database

The eval answers from penn_demo (frozen 2026-10-17), so the expectations must be checked against the
same records: a listing added to the live database later otherwise fails a correct "no record" answer.
event_closures_*.json are left alone (freeze_closures.py); *_area.json are re-taken with freeze_area.py.
Prints what each snapshot gained or lost.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from penn import tools  # noqa: E402
from penn.eval.__main__ import DEFAULT_DB, use_db  # noqa: E402

import freeze_area  # noqa: E402  (eval/, next to this file)

RECORDS = Path(__file__).parent / "records"


def refreeze(path: Path, db: str) -> tuple[set[str], set[str]]:
    snap = json.loads(path.read_text(encoding="utf-8"))
    token = tools.LEGS.set(snap.get("seg_ids") or None)
    try:
        result = tools.search_events(snap["cells"], snap["start"], snap["end"])
    finally:
        tools.LEGS.reset(token)
    old = {e["event_id"] for e in snap["result"]["events"]}
    new = {e["event_id"] for e in result["events"]}
    snap |= {"db": db, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "result": result}
    path.write_text(json.dumps(snap, indent=1, default=str), encoding="utf-8")
    return new - old, old - new


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args(argv)
    use_db(args.db)
    for path in sorted(RECORDS.glob("*.json")):
        if path.name.startswith("event_closures_"):
            continue
        if path.name.endswith("_area.json"):        # trip questions: brief and full, from the question itself
            snap = json.loads(path.read_text(encoding="utf-8"))
            before = {e["event_id"] for e in snap["result"]["events"]}
            pin = tuple(snap["pin"]) if snap.get("pin") else None
            freeze_area.freeze(snap["question"], None if pin else snap["place"], pin, snap["now"], args.db)
            after = {e["event_id"] for e in json.loads(path.read_text(encoding="utf-8"))["result"]["events"]}
            print(f"{path.name}: +{len(after - before)} -{len(before - after)}")
            continue
        added, gone = refreeze(path, args.db)
        print(f"{path.name}: +{len(added)} -{len(gone)}" + (f"  gone: {sorted(gone)}" if gone else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
