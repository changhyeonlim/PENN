"""Check eval/questions.jsonl against the frozen records in eval/records/. No database needed.

Rules:
- every expect_id exists in the question's snapshot (event_closures_*.json for street_history)
- the snapshot was taken with the same cells, start and end as the question
- no question text contains a record ID
- negative questions expect no IDs; every other type expects at least one
- answer_shape, when set, is brief or full; trip questions set brief
- a negative's absent_terms really are absent from its snapshot

    python eval/validate_questions.py            # validate the written file
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
RECORDS = HERE / "records"
TYPES = {"lookup", "specific", "negative", "trap", "multi_day", "street_history", "trip", "place"}
REQUIRED = ("id", "place", "cells", "start", "end", "window", "question", "type",
            "expect_ids", "must_say", "must_not_say", "trap", "source_query",
            "required_facts", "forbidden", "expected_tools")
ID_IN_TEXT = re.compile(
    r"\[id:|\b(?:lane_closures|event_closures):|\bEUN\d+|\bG?\d{4}-\d{5}\b|\b\d{7,9}\b")


def _load(name: str) -> dict:
    return json.loads((RECORDS / name).read_text(encoding="utf-8"))


def _closure_ids() -> set[str]:
    ids = set()
    for p in RECORDS.glob("event_closures_*.json"):
        ids |= {"event_closures:" + r["closure_id"] for r in json.loads(p.read_text(encoding="utf-8"))["rows"]}
    return ids


def check(q: dict, closure_ids: set[str] | None = None) -> list[str]:
    """Return the reasons a question is rejected (empty list = valid)."""
    errs = [f"missing field {f}" for f in REQUIRED if f not in q]
    if errs:
        return errs
    if q["type"] not in TYPES:
        errs.append(f"unknown type {q['type']}")
    if ID_IN_TEXT.search(q["question"]):
        errs.append("question text contains a record ID")
    if not q["must_say"]:
        errs.append("must_say is empty")
    if not q["required_facts"] or any(not g for g in q["required_facts"]):
        errs.append("required_facts is empty or has an empty group")
    for pat in [a[3:] for g in q["required_facts"] for a in g if a.startswith("re:")] + q["forbidden"]:
        try:
            re.compile(pat)
        except re.error as e:
            errs.append(f"bad pattern {pat!r}: {e}")
    if q["type"] == "negative":
        if q["expect_ids"]:
            errs.append("negative question expects IDs")
    elif not q["expect_ids"]:
        errs.append("non-negative question expects no IDs")

    if q.get("answer_shape") not in (None, "brief", "full", "place"):
        errs.append(f"answer_shape {q['answer_shape']!r} is not brief, full or place")
    if q.get("difficulty") not in (None, "easy", "medium", "hard"):
        errs.append(f"difficulty {q['difficulty']!r} is not easy, medium or hard")
    if q["type"] == "place" and q.get("answer_shape") != "place":
        errs.append("place questions are answered with the place briefing")
    if q["type"] == "trip" and q.get("answer_shape") != "brief":
        errs.append("trip questions are answered with the brief")
    if q["type"] == "street_history":
        ids = closure_ids if closure_ids is not None else _closure_ids()
        errs += [f"{i} not in event_closures snapshots" for i in q["expect_ids"] if i not in ids]
        return errs

    snap_name = q.get("snapshot")
    if not snap_name or not (RECORDS / snap_name).exists():
        return errs + [f"snapshot {snap_name!r} not found"]
    snap = _load(snap_name)
    if (snap["cells"], snap["start"], snap["end"]) != (q["cells"], q["start"], q["end"]):
        errs.append("cells/start/end differ from the snapshot")
    events = snap["result"]["events"]
    have = {e["event_id"] for e in events}
    errs += [f"{i} not in {snap_name}" for i in q["expect_ids"] if i not in have]
    for term in q.get("absent_terms", []):
        hits = [e["event_id"] for e in events
                if term.lower() in (e["title"] + " " + e["description"]).lower()]
        if hits:
            errs.append(f"absent term {term!r} found in {hits}")
    return errs


def validate(questions: list[dict]) -> tuple[list[dict], list[tuple[str, list[str]]]]:
    closure_ids = _closure_ids()
    ok, rejects, seen = [], [], set()
    for q in questions:
        errs = check(q, closure_ids)
        if q.get("id") in seen:
            errs.append("duplicate id")
        seen.add(q.get("id"))
        (rejects.append((q.get("id"), errs)) if errs else ok.append(q))
    return ok, rejects


def report(ok: list[dict], rejects: list[tuple[str, list[str]]]) -> None:
    counts = collections.Counter(q["type"] for q in ok)
    print("valid:", len(ok), dict(sorted(counts.items())))
    traps = collections.Counter(q["trap"] for q in ok if q["trap"])
    print("traps:", dict(sorted(traps.items())))
    print("difficulty:", dict(sorted(collections.Counter(str(q.get("difficulty")) for q in ok).items())))
    print("rejects:", len(rejects))
    for qid, errs in rejects:
        print(f"  {qid}: " + "; ".join(errs))


if __name__ == "__main__":
    qs = [json.loads(l) for l in (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    ok, rejects = validate(qs)
    report(ok, rejects)
    sys.exit(1 if rejects else 0)
