"""Entry point for Nebius Serverless Jobs (or cron, locally).

    python jobs/run_job.py lane_closures      # daily: events + today's snapshot
    python jobs/run_job.py philly_311 2025    # backfill one year (repeat per year)
    python jobs/run_job.py philly_311         # daily: current year
    python jobs/run_job.py recurring_events   # one-time curated backfill
    python jobs/run_job.py event_closures     # announced closures; all rows or none
    python jobs/run_job.py geocode_closures   # match closures to street centerline -> events
    python jobs/run_job.py geocode_closures --export broad-street-run   # also write GeoJSON

Each run writes a row to penn.ingest_runs, which feeds the map's freshness stamps.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from penn import db  # noqa: E402
from penn.config import LOCAL_TZ  # noqa: E402
from penn.geo import streets  # noqa: E402
from penn.ingestion import event_closures, lane_closures, recurring_events, requests311  # noqa: E402

CENTERLINE = ROOT / "data" / "centerline" / "street_centerline.geojson"
ROUTES = ROOT / "data" / "closure_routes.csv"
EXPORTS = ROOT / "data" / "exports"


class InvalidRows(Exception):
    """A source file failed validation; nothing was written."""


def _record_run(source: str, started: datetime, rows: int, ok: bool, message: str = "") -> None:
    db.client().insert(
        "ingest_runs",
        [[source, started, datetime.now(timezone.utc), rows, ok, message]],
        column_names=["source", "started_at", "finished_at", "rows_written", "ok", "message"],
    )


def _raw(source: str, items: list[tuple[str, dict]]) -> None:
    if items:
        db.client().insert("raw_records", [[source, rid, json.dumps(p)] for rid, p in items],
                           column_names=["source", "source_record_id", "payload"])


def run_lane_closures() -> int:
    feats = list(lane_closures.fetch_features())
    _raw(lane_closures.SOURCE, [(str(f["properties"].get("objectid")), f) for f in feats])
    today = datetime.now(LOCAL_TZ).date()
    db.insert_snapshots(lane_closures.snapshot_rows(feats, today))
    return db.insert_events(lane_closures.group_by_permit(feats))


def run_311(year: int | None) -> int:
    year = year or datetime.now(LOCAL_TZ).year
    rows = list(requests311.fetch_year(year))
    # event_type is "reported_issue" until the Nano classifier maps service names to categories.
    events = [e for e in (requests311.normalize(r) for r in rows) if e]
    return db.insert_events(events)


def run_recurring() -> int:
    return db.insert_events(recurring_events.load_all(ROOT / "data" / "recurring_events"))


def run_event_closures() -> int:
    rows, errors = event_closures.load_all(ROOT / "data" / "event_closures")
    for e in errors:
        print(f"error: {e.file}:{e.line}: {e.reason}", file=sys.stderr)
    if errors:
        # A bad file never half-loads: refuse the whole run.
        raise InvalidRows(f"{len(errors)} invalid rows, nothing inserted; first: "
                          f"{errors[0].file}:{errors[0].line}: {errors[0].reason}")
    return db.insert_closures(rows)


def run_geocode_closures(export: list[str]) -> int:
    if not CENTERLINE.exists():
        print(f"downloading street centerline to {CENTERLINE.relative_to(ROOT)} ...")
        streets.download_centerline(CENTERLINE)
    net = streets.Network(streets.load_centerline(CENTERLINE),
                          equivalents=streets.EQUIVALENTS, loose_types=True)
    rows = [_as_utc(r) for r in
            db.query("SELECT * FROM penn.event_closures FINAL ORDER BY series, start_date, closure_id")]
    routes, errors = event_closures.load_routes(ROUTES)
    unknown = sorted(set(routes) - {r["closure_id"] for r in rows})
    errors += [event_closures.RowError(ROUTES.name, 0, f"no event_closures row {cid}") for cid in unknown]
    for e in errors:
        print(f"error: {e.file}:{e.line}: {e.reason}", file=sys.stderr)
    if errors:
        raise InvalidRows(f"{len(errors)} invalid route rows, nothing written; first: {errors[0].reason}")
    results = [(r, streets.resolve_route(routes[r["closure_id"]]["legs"], net, routes[r["closure_id"]]["note"])
                if r["closure_id"] in routes else streets.resolve(r, net)) for r in rows]

    now = datetime.now(timezone.utc)
    events = [event_closures.to_event(r, res.segments, res.notes, now) for r, res in results if res.ok]
    n = db.insert_events(events)
    # Only rows whose flag changes are rewritten; the newer updated_at wins in the ReplacingMergeTree.
    flips = [{**r, "geocoded": res.ok, "updated_at": now} for r, res in results if r["geocoded"] != res.ok]
    db.insert_closures(flips)

    _print_geocode_summary(results)
    for series in export:
        feats = [(res.segments, {
            "closure_id": r["closure_id"], "street": r["street"], "from_street": r["from_street"],
            "to_street": r["to_street"], "start_ts": str(r["start_ts"] or ""),
            "end_ts": str(r["end_ts"] or ""), "seg_ids": [s.seg_id for s in res.segments],
            "notes": res.notes,
        }) for r, res in results if r["series"] == series and res.ok]
        path = EXPORTS / f"{series}.geojson"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(streets.to_geojson(feats), indent=1))
        total = sum(r["series"] == series for r, _ in results)
        print(f"exported {len(feats)} of {total} {series} closures to {path.relative_to(ROOT)}")
    return n


def _as_utc(row: dict) -> dict:
    """clickhouse-connect returns DateTime64('UTC') values as naive datetimes, and inserts naive
    datetimes as local time. Writing a row back unchanged would shift it by the UTC offset."""
    return {k: v.replace(tzinfo=timezone.utc) if isinstance(v, datetime) and v.tzinfo is None else v
            for k, v in row.items()}


def _print_geocode_summary(results: list[tuple[dict, "streets.Resolution"]]) -> None:
    kinds = ["from_to", "block_range", "named", "route", "one_end", "area"]
    ok = sum(res.ok for _, res in results)
    print(f"\nresolved {ok} of {len(results)} rows ({100 * ok / len(results):.1f}%)\n")
    print(f"{'kind':12} {'resolved':>8} {'total':>6}")
    for k in kinds:
        tot = [res for _, res in results if res.kind == k]
        print(f"{k:12} {sum(r.ok for r in tot):8} {len(tot):6}")
    notes = [(r, res) for r, res in results if res.ok and res.notes]
    if notes:
        print("\nresolved with a note (name substitution or curated route):")
        for r, res in notes:
            print(f"  {r['closure_id']:42} {'; '.join(res.notes)}")
    bad = [(r, res) for r, res in results if not res.ok]
    print(f"\nunresolved ({len(bad)}):")
    print(f"  {'closure_id':42} {'kind':11} {'street':40} {'from':24} {'to':24} reason")
    for r, res in bad:
        print(f"  {r['closure_id']:42} {res.kind:11} {r['street'][:40]:40} "
              f"{r['from_street'][:24]:24} {r['to_street'][:24]:24} {res.reason}")


def main(argv: list[str]) -> None:
    source = argv[1] if len(argv) > 1 else ""
    started = datetime.now(timezone.utc)
    try:
        if source == "lane_closures":
            n = run_lane_closures()
        elif source == "philly_311":
            n = run_311(int(argv[2]) if len(argv) > 2 else None)
        elif source == "recurring_events":
            n = run_recurring()
        elif source == "event_closures":
            n = run_event_closures()
        elif source == "geocode_closures":
            export = argv[argv.index("--export") + 1:] if "--export" in argv else []
            n = run_geocode_closures(export)
        else:
            raise SystemExit(__doc__)
        _record_run(source, started, n, True)
        print(f"{source}: wrote {n} events")
    except SystemExit:
        raise
    except InvalidRows as exc:
        _record_run(source, started, 0, False, str(exc)[:500])
        raise SystemExit(f"{source}: {exc}") from None
    except Exception as exc:  # record the failure, then fail the job
        _record_run(source, started, 0, False, repr(exc)[:500])
        raise


if __name__ == "__main__":
    logging.basicConfig(format="%(levelname)s: %(message)s", level=logging.WARNING)
    main(sys.argv)
