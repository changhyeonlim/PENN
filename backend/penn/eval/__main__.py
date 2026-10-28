"""python -m penn.eval                     run all questions
python -m penn.eval --only tr04,sh01     run some
python -m penn.eval --type trap          run one type (lookup, specific, negative, trap, multi_day, street_history)
python -m penn.eval --workers 1          run one at a time (default 4 in parallel)
python -m penn.eval --db penn            run against the live database instead of the frozen
                                         penn_demo copy (expectations were written from penn_demo)
python -m penn.eval --rescore eval/results/run-X.json   re-score saved answers with the current
                                         questions and scorers (no model calls)
python -m penn.eval --subset baseline12  only the questions in eval/subsets/baseline12.json
python -m penn.eval --models nvidia/a,qwen/b --repeats 3
                                         run each model 3 times (repeats interleaved, so a slow hour on
                                         the endpoint does not land on one model), then write
                                         eval/leaderboard.json from those runs
python -m penn.eval --leaderboard eval/baselines/*.json eval/results/run-X.json
                                         build eval/leaderboard.json from saved runs (no model calls)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from ..config import settings
from . import leaderboard, report, scorers
from .runner import run_all
from .schema import EVAL_DIR, Question, Result, load_questions, load_results


# The frozen copy of 2026-10-17 (the penn_demo database): the live database keeps changing, and an answer
# must not fail because a listing appeared after the expectations were written.
DEFAULT_DB = "penn_demo"


def use_db(name: str) -> None:
    """Answer from this database. Settings is frozen and read once at import, so the field is set
    directly; it must happen before anything queries, since db.client() is built lazily per thread."""
    object.__setattr__(settings, "clickhouse_db", name)


def subset_ids(name: str) -> set[str]:
    """The question IDs of eval/subsets/<name>.json, from every group in it."""
    groups = json.loads((EVAL_DIR / "subsets" / f"{name}.json").read_text(encoding="utf-8"))["groups"]
    return {i for g in groups.values() for i in g}


def _slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "-", model.split("/")[-1]).strip("-").lower()


def _report(qs: list[Question], results: list[Result], db: str, tag: str = "") -> Path:
    for q, r in zip(qs, results):
        scorers.score(q, r)
    print()
    report.print_table(qs, results)
    report.print_failures(qs, results)
    summary = report.summarize(qs, results) | {"db": db}
    path = report.write_results(qs, results, summary, tag)
    print(f"\nPASSED {summary['passed']}/{summary['total']} (held, not counted: {summary['held']})  "
          f"facts {summary['mean_fact_coverage']:.0%}  id recall {summary['mean_id_recall'] or 0:.0%}  "
          f"gave up {summary['gave_up']}  rewrites {summary['rewrites']}  errors {summary['errors']}")
    print(f"by difficulty: {summary['by_difficulty']}")
    print(f"by type: {summary['by_type']}")
    print(f"saved {path}")
    return path


def _board(paths: list[Path]) -> None:
    board = leaderboard.build(paths)
    print()
    leaderboard.print_board(board)
    print(f"wrote {leaderboard.write(board)}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m penn.eval")
    p.add_argument("--only", help="comma-separated question IDs")
    p.add_argument("--type", help="only questions of this type")
    p.add_argument("--subset", help="only the questions in eval/subsets/<name>.json")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--rescore", type=Path, help="saved results file to re-score instead of running")
    p.add_argument("--db", default=DEFAULT_DB,
                   help=f"ClickHouse database to answer from (default {DEFAULT_DB}, the frozen demo copy)")
    p.add_argument("--models", help="comma-separated Token Factory model IDs to compare (see GET /v1/models)")
    p.add_argument("--repeats", type=int, default=1, help="runs per model (answers vary even at temperature 0)")
    p.add_argument("--leaderboard", type=Path, nargs="+", metavar="RUN",
                   help="saved runs to compare in eval/leaderboard.json, instead of running")
    args = p.parse_args(argv)
    use_db(args.db)

    sys.stdout.reconfigure(encoding="utf-8")
    if args.leaderboard:
        _board(args.leaderboard)
        return 0
    qs = load_questions()
    if args.only:
        wanted = set(args.only.split(","))
        qs = [q for q in qs if q.id in wanted]
    if args.type:
        qs = [q for q in qs if q.type == args.type]
    if args.subset:
        wanted = subset_ids(args.subset)
        qs = [q for q in qs if q.id in wanted]
    if not qs:
        print("no questions selected")
        return 2

    if args.rescore:
        saved = {r.id: r for r in load_results(args.rescore)}
        qs = [q for q in qs if q.id in saved]
        print(f"re-scoring {len(qs)} saved answers from {args.rescore}")
        _report(qs, [saved[q.id] for q in qs], "rescored")
        return 0

    models = [m.strip() for m in args.models.split(",") if m.strip()] if args.models else [None]
    paths = []
    for rep in range(1, max(1, args.repeats) + 1):
        for model in models:
            label = model or settings.model_chat or settings.model_super
            print(f"running {len(qs)} questions on {label} (run {rep}/{args.repeats}) with {args.workers} workers")
            results = run_all(qs, workers=args.workers, model=model,
                              on_done=lambda q, r: print(f"  {q.id} done in {r.latency_ms / 1000:.1f}s", flush=True))
            paths.append(_report(qs, results, args.db, _slug(model) if args.models else ""))
    if args.models or args.repeats > 1:
        _board(paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
