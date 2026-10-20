"""Print a pass/fail table and write a JSON results file for the slide."""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .schema import EVAL_DIR, Question, Result


DIFFICULTIES = ("easy", "medium", "hard")


def _frac(x: float | None) -> str:
    return "-" if x is None else f"{x:.0%}"


def summarize(questions: list[Question], results: list[Result]) -> dict:
    """Headline numbers cover questions not on hold; held ones are counted separately."""
    by_type: dict[str, list[bool]] = defaultdict(list)
    by_difficulty: dict[str, list[bool]] = defaultdict(list)
    held = [r for q, r in zip(questions, results) if q.hold]
    pairs = [(q, r) for q, r in zip(questions, results) if not q.hold]
    for q, r in pairs:
        by_type[q.trap and "trap:" + q.trap or q.type].append(r.passed)
        by_difficulty[q.difficulty or "unrated"].append(r.passed)
    results = [r for _, r in pairs]
    n = len(results)
    recalls = [r.scores["id_recall"] for r in results if r.scores.get("id_recall") is not None]
    return {
        "passed": sum(r.passed for r in results),
        "total": n,
        "held": f"{sum(r.passed for r in held)}/{len(held)}",
        "by_type": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_type.items())},
        "by_difficulty": {k: f"{sum(by_difficulty[k])}/{len(by_difficulty[k])}"
                          for k in DIFFICULTIES + ("unrated",) if k in by_difficulty},
        "mean_fact_coverage": round(sum(r.scores.get("fact_coverage", 0) for r in results) / n, 3) if n else 0,
        "mean_id_recall": round(sum(recalls) / len(recalls), 3) if recalls else None,
        "citation_valid_all": all(r.scores.get("citation_valid") == 1.0 for r in results),
        "gave_up": sum(r.gave_up for r in results),
        "rewrites": sum(r.drafts > 1 for r in results),
        "errors": sum(r.error is not None for r in results),
        "mean_latency_ms": int(sum(r.latency_ms for r in results) / n) if n else 0,
        "mean_prompt_tokens": int(sum(r.prompt_tokens for r in results) / n) if n else 0,
        "models": sorted({r.model for r in results if r.model}),
    }


def print_table(questions: list[Question], results: list[Result]) -> None:
    head = f"{'id':<5} {'type':<15} {'pass':<5}{'tool':<4} {'facts':>5} {'ids':>5} {'ret':>5} {'cite':>5} {'forb':>4} {'drafts':>6} {'sec':>5} {'ptok':>6}"
    print(head)
    print("-" * len(head))
    for q, r in zip(questions, results):
        s = r.scores
        verdict = ("PASS" if r.passed else "fail") + ("*" if q.hold else "")
        print(f"{q.id:<5} {(q.trap or q.type)[:15]:<15} {verdict:<5}"
              f"{'ok' if s.get('tool_match') else 'NO':<4} {_frac(s.get('fact_coverage')):>5} "
              f"{_frac(s.get('id_recall')):>5} {_frac(s.get('retrieval_recall')):>5} "
              f"{_frac(s.get('citation_valid')):>5} {len(s.get('forbidden_hits', [])):>4} "
              f"{r.drafts:>6} {r.latency_ms / 1000:>5.1f} {r.prompt_tokens:>6}")


def print_failures(questions: list[Question], results: list[Result]) -> None:
    for q, r in zip(questions, results):
        if r.passed:
            continue
        print(f"\n--- {q.id}: {q.question}")
        if r.error:
            print("  ERROR:", r.error.splitlines()[0])
            continue
        if r.gave_up:
            print("  gave up after the citation check")
        if not r.scores["tool_match"]:
            print(f"  tools: expected {q.expected_tools}, called {r.tools_called}")
        for i in r.scores["missing_facts"]:
            hint = q.must_say[i] if i < len(q.must_say) else ""
            print(f"  missing fact {i}: {q.required_facts[i][:4]}  ({hint[:80]})" if hint else
                  f"  missing fact {i}: {q.required_facts[i][:4]}")
        if r.scores.get("shape_match") is False:
            print(f"  answer shape: expected {q.answer_shape}, code chose {r.answer_shapes or 'no search'}")
        for h in r.scores["forbidden_hits"]:
            print(f"  forbidden: ...{h}...")
        if r.scores["citation_valid"] < 1:
            print(f"  cites IDs the tools did not return: {sorted(set(r.cited) - set(r.returned_ids))}")


def write_results(questions: list[Question], results: list[Result], summary: dict, tag: str = "") -> Path:
    """tag names the model in the file name when one invocation runs several."""
    out_dir = EVAL_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"run-{datetime.now():%Y%m%d-%H%M%S}{'-' + tag if tag else ''}.json"
    payload = {
        "summary": summary,
        "results": [asdict(r) | {"question": q.question, "type": q.type, "trap": q.trap}
                    for q, r in zip(questions, results)],
    }
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return path
