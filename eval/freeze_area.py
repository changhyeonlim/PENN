"""Freeze search_events on a neighborhood, a landmark or a picked point into eval/records/<slug>_<date>_area.json.

    python eval/freeze_area.py "Rittenhouse" "Rittenhouse next week"
    python eval/freeze_area.py --pin -75.1503,39.9489 "What's going on around here this weekend?"
    python eval/freeze_area.py "Rittenhouse Square" "What's happening in Rittenhouse Square on Sunday, October 25?"

Trip questions ("Rittenhouse next week") get the brief answer, so the window is resolved from the
question itself at the frozen copy's pinned time (penn_demo: 2026-10-17 10:00), exactly as the API
would. The file keeps every record (full detail), so validate_questions.py can check expect_ids, and
the brief the model sees (impact tiers), so expectations are written from it, never from memory.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from penn import timeutil, tools  # noqa: E402
from penn.agent.context import Context  # noqa: E402
from penn.eval.__main__ import DEFAULT_DB, use_db  # noqa: E402
from penn.geo import locate  # noqa: E402

RECORDS = Path(__file__).parent / "records"
PINNED_NOW = "2026-10-17T10:00:00-04:00"    # penn_demo.demo_info.pinned_now


def freeze(question: str, area: str | None = None, pin: tuple[float, float] | None = None,
           now: str = PINNED_NOW, db: str = DEFAULT_DB) -> Path:
    use_db(db)
    at = datetime.fromisoformat(now)
    loc = locate.from_click(*pin) if pin else locate.from_text(area or "")
    if not loc.cells or (not pin and loc.method not in ("area", "landmark")):
        raise SystemExit(f"{area!r} did not resolve to a neighborhood or a landmark ({loc.method})")
    win = timeutil.resolve_window(question, at)
    if win is None:
        raise SystemExit(f"no time read in {question!r}")
    ctx = Context(at, date(2016, 1, 1), message=question)
    ctx.add_place(loc, {"text": area} if area else {"lat": pin[1], "lon": pin[0]}, "this_place")
    ctx.add_window(win, "this_window")
    brief = ctx.search_events("this_place", "this_window")
    full = tools.search_events(loc.cells, win.start.isoformat(), win.end.isoformat())
    slug = re.sub(r"[^a-z0-9]+", "-", (area or f"pin {pin[1]:.4f} {pin[0]:.4f}").lower()).strip("-")
    out = RECORDS / f"{slug}_{win.start:%Y-%m-%d}_area.json"
    out.write_text(json.dumps({
        "place": loc.label, "slug": slug, "method": "click" if pin else loc.method, "cells": loc.cells,
        "start": win.start.isoformat(), "end": win.end.isoformat(), "window": win.label,
        "question": question, "now": now, "db": db, "pin": list(pin) if pin else None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool_call": f"search_events({'from_click' if pin else 'from_text'}({pin or area!r}).cells, window of "
                     f"{question!r} at {now})",
        "brief": brief, "result": full,
    }, indent=1, default=str), encoding="utf-8")
    print(f"{out.name}: {full['count']} records, answer shape {brief['searched']['answer_shape']}")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("area", nargs="?", help="a neighborhood name (omit with --pin)")
    ap.add_argument("question")
    ap.add_argument("--pin", help="lon,lat of a point picked on the map")
    ap.add_argument("--now", default=PINNED_NOW)
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args()
    pin = tuple(float(x) for x in a.pin.split(",")) if a.pin else None
    freeze(a.question, None if pin else a.area, pin, a.now, a.db)
