"""Compare models across saved runs: eval/leaderboard.json, for the README table and the /eval page.

Every run is re-scored with the current questions and scorers, and only the questions every run
answered count, so runs from different days compare on the same questions. Models are ranked only
within their size bracket (eval/models.json): a 1T model beating a 120B one says nothing about the
choice of the 120B one. Runs of one model are its repeats: pass counts are given per run (min, mean, max), since answers vary between runs even
at temperature 0. Latency and format numbers pool every answer of every repeat.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from ..agent.citations import CITE
from ..agent.loop import strip_think
from . import scorers
from .schema import EVAL_DIR, Question, Result, load_questions, load_results

LEADERBOARD = EVAL_DIR / "leaderboard.json"
MODELS = EVAL_DIR / "models.json"


def load_registry(path: Path = MODELS) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

# The one form the prompt asks for. CITE also accepts 【...】, 【id:...】 and 【{"id": ...}】, which the
# loop and the page normalize; how often a model needs that is part of its score.
STRICT = re.compile(r"\[id:[a-z0-9_]+:[A-Za-z0-9_:\-\.]+\]")
# Anything in brackets that names a record source: a citation attempt, whatever its form.
ATTEMPT = re.compile(r"[\[【][^\]】\n]{0,40}?\b(?:lane_closures|event_closures|philly_311|visitphilly|ticketmaster|web):"
                     r"[^\]】\n]*[\]】]")


def percentile(xs: list[float], p: float) -> float | None:
    """Linear interpolation between closest ranks (numpy's default)."""
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _pcts(xs: list[float]) -> dict:
    return {f"p{int(p * 100)}": None if (v := percentile(xs, p)) is None else round(v, 2)
            for p in (0.5, 0.9, 0.99)}


def format_stats(results: list[Result]) -> dict:
    """How each draft wrote its citations, before the loop normalized or rejected them.

    attempts: bracketed record IDs in any form; strict: written exactly as [id:<event_id>];
    accepted: read by the lenient CITE parser (strict plus the 【】 forms); missed = attempts the
    parser could not read, which fail the citation check."""
    attempts = strict = accepted = 0
    first_total = first_strict = think = 0
    for r in results:
        if any("<think>" in t.lower() or "</think>" in t.lower() for t in r.draft_texts):
            think += 1
        for i, raw in enumerate(r.draft_texts):
            t = strip_think(raw)
            a, s, c = len(ATTEMPT.findall(t)), len(STRICT.findall(t)), len(CITE.findall(t))
            attempts, strict, accepted = attempts + a, strict + s, accepted + c
            if i == 0 and a:
                first_total += 1
                first_strict += s >= a
    return {
        "cite_attempts": attempts,
        "strict_share": round(strict / attempts, 3) if attempts else None,
        "accepted_share": round(min(accepted, attempts) / attempts, 3) if attempts else None,
        "missed": max(attempts - accepted, 0),
        "strict_first_drafts": round(first_strict / first_total, 3) if first_total else None,
        "answers_with_think_tags": think,
    }


def _run_model(summary: dict, results: list[Result]) -> str:
    models = sorted({r.model for r in results if r.model}) or summary.get("models") or ["unknown"]
    return "+".join(models)


def model_row(model: str, runs: list[tuple[str, list[tuple[Question, Result]]]]) -> dict:
    """One model's numbers over its runs (each run: file name, scored (question, result) pairs)."""
    passed = [sum(r.passed for q, r in pairs if not q.hold) for _, pairs in runs]
    totals = [sum(not q.hold for q, _ in pairs) for _, pairs in runs]
    every = [(q, r) for _, pairs in runs for q, r in pairs if not q.hold]
    results = [r for _, r in every]
    ok = [r for r in results if r.error is None]
    calls = [c for r in ok for c in r.calls]
    rates = [c["output_tokens"] / (c["latency_ms"] / 1000)
             for c in calls if c.get("output_tokens") and c.get("latency_ms")]
    by_type: dict[str, list[bool]] = defaultdict(list)
    by_difficulty: dict[str, list[bool]] = defaultdict(list)
    for q, r in every:
        by_type[q.trap and "trap:" + q.trap or q.type].append(r.passed)
        by_difficulty[q.difficulty or "unrated"].append(r.passed)
    n = len(results) or 1
    return {
        "model": model,
        "runs": len(runs),
        "files": [f for f, _ in runs],
        "total": totals[0] if totals else 0,
        "passed": {"per_run": passed, "min": min(passed, default=0), "max": max(passed, default=0),
                   "mean": round(sum(passed) / len(passed), 2) if passed else 0},
        "pass_rate": round(sum(passed) / sum(totals), 3) if sum(totals) else 0,
        "latency_s": _pcts([r.latency_ms / 1000 for r in ok]),
        "call_latency_s": _pcts([c["latency_ms"] / 1000 for c in calls if c.get("latency_ms") is not None]),
        "calls_per_answer": round(len(calls) / len(ok), 2) if ok else None,
        "output_tokens_per_s": _pcts(rates)["p50"],
        "prompt_tokens_mean": int(sum(r.prompt_tokens for r in ok) / len(ok)) if ok else 0,
        "output_tokens_mean": int(sum(r.output_tokens for r in ok) / len(ok)) if ok else 0,
        "format": format_stats(ok),
        "rewrite_rate": round(sum(r.drafts > 1 for r in results) / n, 3),
        "gave_up": sum(r.gave_up for r in results),
        "errors": sum(r.error is not None for r in results),
        "unparsed_tool_args": sum("_unparsed" in t.get("args", {}) for r in results for t in r.tool_args),
        "by_type": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_type.items())},
        "by_difficulty": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_difficulty.items())},
    }


def build(paths: list[Path], questions: list[Question] | None = None, registry: dict | None = None) -> dict:
    """Re-score the runs in `paths` on the questions they all answered and compare models, ranked
    within each size bracket. A model missing from the registry goes in "unbracketed"."""
    qmap = {q.id: q for q in (questions or load_questions())}
    registry = registry or load_registry()
    order = registry["brackets"] + ["unbracketed"]
    runs = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        results = load_results(path)
        runs.append((path, payload.get("summary", {}), results))
    common = set(qmap)
    for _, _, results in runs:
        common &= {r.id for r in results}

    by_model: dict[str, list] = defaultdict(list)
    dbs = set()
    for path, summary, results in runs:
        pairs = [(qmap[r.id], scorers.score(qmap[r.id], r)) for r in results if r.id in common]
        pairs.sort(key=lambda p: p[0].id)
        by_model[_run_model(summary, results)].append((path.name, pairs))
        dbs.add(summary.get("db", "unknown"))

    rows = []
    for m, rs in by_model.items():
        info = registry["models"].get(m, {})
        rows.append({"bracket": info.get("bracket", "unbracketed"), "total_b": info.get("total_b"),
                     "active_b": info.get("active_b"), "thinking": info.get("thinking")} | model_row(m, rs))
    rows.sort(key=lambda row: (order.index(row["bracket"]), -row["pass_rate"], row["latency_s"]["p90"] or 0))
    held = sorted(i for i in common if qmap[i].hold)
    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "questions": len(common) - len(held),
        "held_not_counted": held,
        "dropped": sorted({r.id for _, _, rs in runs for r in rs} - common),
        "db": sorted(dbs),
        "models": rows,
    }


def write(board: dict, path: Path = LEADERBOARD) -> Path:
    path.write_text(json.dumps(board, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def _num(x, fmt="{:.1f}") -> str:
    return "-" if x is None else fmt.format(x)


def print_board(board: dict) -> None:
    print(f"{board['questions']} questions in every run (db {', '.join(board['db'])})"
          + (f"; dropped {len(board['dropped'])} not in every run" if board["dropped"] else ""))
    head = (f"{'model':<44} {'runs':>4} {'pass min/mean/max':>18} {'p50':>5} {'p90':>5} {'p99':>5} "
            f"{'out tok':>7} {'strict':>6} {'parsed':>6} {'rewr':>5} {'err':>4}")
    print(head)
    print("-" * len(head))
    bracket = None
    for m in board["models"]:
        if m["bracket"] != bracket:
            bracket = m["bracket"]
            print(f"[{bracket}]")
        p, lat, f = m["passed"], m["latency_s"], m["format"]
        passed = f"{p['min']}/{p['mean']:g}/{p['max']} of {m['total']}"
        print(f"{m['model'][-44:]:<44} {m['runs']:>4} {passed:>18} "
              f"{_num(lat['p50']):>5} {_num(lat['p90']):>5} {_num(lat['p99']):>5} {m['output_tokens_mean']:>7} "
              f"{_num(f['strict_share'], '{:.0%}'):>6} {_num(f['accepted_share'], '{:.0%}'):>6} "
              f"{m['rewrite_rate']:>5.0%} {m['errors']:>4}")
