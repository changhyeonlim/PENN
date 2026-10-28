"""PENN's data loads, run by the container's daily refresh (deploy/endpoint/refresh.sh) or by hand.

    python jobs/run_job.py lane_closures      # daily: events + today's snapshot
    python jobs/run_job.py recurring_events   # one-time curated backfill
    python jobs/run_job.py event_closures     # announced closures; all rows or none
    python jobs/run_job.py geocode_closures   # match closures to street centerline -> events
    python jobs/run_job.py geocode_closures --export broad-street-run   # also write GeoJSON
    python jobs/run_job.py visitphilly        # weekly picks from data/snapshots/visitphilly
    python jobs/run_job.py ticketmaster       # ticketed events, next 60 days; drops ones no longer listed
    python jobs/run_job.py press_releases --saved scripts/phila_press   # City closure notices saved by hand:
                                              # file, parse, load, place on the map

Each run writes a row to penn.ingest_runs, which feeds the map's freshness stamps (except a press_releases
run with nothing new).
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
from penn.ingestion import (event_closures, lane_closures, press_releases, recurring_events,  # noqa: E402
                            ticketmaster, visitphilly)
from penn.tools import history  # noqa: E402

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
    n = db.insert_closures(rows)
    if rows:
        # The files are the whole truth (all rows or none), so a closure no longer in them goes, with its map
        # line. A closure renamed in a rerun (a City advisory's events read by name, not "City Advisory
        # <date>") gets a new closure_id; the old row would otherwise draw the same street a second time.
        keep = [r["closure_id"] for r in rows]
        for sql in ("ALTER TABLE event_closures DELETE WHERE closure_id NOT IN %(keep)s",
                    "ALTER TABLE events DELETE WHERE source = 'event_closures' AND source_record_id NOT IN %(keep)s"):
            db.client().command(sql, parameters={"keep": keep}, settings={"mutations_sync": 2})
    return n


def run_ticketmaster() -> int:
    """Ticketmaster's terms allow keeping events only as long as the service needs them: load the next
    60 days, then delete every ticketmaster row this run did not return (past, removed, cancelled).
    A run that returns nothing deletes nothing, so an API outage cannot empty the map."""
    rep = ticketmaster.FetchReport()
    raw = ticketmaster.fetch(datetime.now(LOCAL_TZ), client=None, rep=rep)
    ticketmaster.correct_venues(raw, rep=rep)
    events = ticketmaster.to_events(raw, rep)
    print(f"{rep.calls} API calls, {len(raw)} events listed -> {rep.events} in the city")
    print(f"skipped, cancelled ({len(rep.skipped_status)}); add-ons ({len(rep.add_ons)}); outside the city ({len(rep.outside)}); "
          f"no venue location ({len(rep.no_location)})")
    print(f"venue pins moved to the City's address point ({len(rep.moved)}); duplicate performances dropped "
          f"({rep.duplicates})")
    for s in rep.moved:
        print(f"  moved {s}")
    for s in rep.outside + rep.no_location:
        print(f"  {s}")
    n = db.insert_events(events)
    if events:
        keep = [e.event_id for e in events]
        db.client().command("ALTER TABLE events DELETE WHERE source = %(s)s AND event_id NOT IN %(keep)s",
                            parameters={"s": ticketmaster.SOURCE, "keep": keep}, settings={"mutations_sync": 2})
    return n


def run_visitphilly() -> int:
    """Every snapshot, every run: rows are keyed by item, so reloading replaces rather than adds."""
    events, rep, raw = visitphilly.load_all(ROOT / "data" / "snapshots" / "visitphilly")
    _raw(visitphilly.SOURCE, raw)
    print(f"{rep.files} snapshots, {rep.items} items -> {rep.events} events")
    print(f"skipped, no usable date ({len(rep.unparsed)}):")
    for s in rep.unparsed:
        print(f"  {s}")
    print(f"skipped, outside the city ({len(rep.outside)}):")
    for s in rep.outside:
        print(f"  {s}")
    return db.insert_events(events)


def run_press_releases(saved: list[str]) -> int | None:
    """p1-11: file City closure notices saved by hand in a browser (files or folders), rewrite
    data/event_closures/press_releases.csv from all of them, then load every closure file and match it to
    the centerline, as event_closures and geocode_closures do. When none of the pages is new or edited,
    nothing is rebuilt and None is returned, so the run isn't stamped as fresh data."""
    snapshots = ROOT / "data" / "snapshots" / "phila_press"
    fetched = press_releases.import_saved([Path(p) for p in saved], snapshots)
    print(f"{fetched.notices} saved pages read: {len(fetched.saved)} new or edited, "
          f"{len(fetched.unchanged)} already filed, {len(fetched.failed)} skipped")
    for f in fetched.saved:
        print(f"  saved {f}")
    for f in fetched.failed:
        print(f"  SKIPPED {f}", file=sys.stderr)
    if not fetched.saved:
        return None
    catalog = {s: r["name"] for s, r in history.catalog().items()}
    rep = press_releases.build_csv(snapshots, ROOT / "data" / "event_closures", catalog)
    print(f"{len(rep.used)} notices -> {rep.rows} closure rows ({len(rep.curated)} left to curated files, "
          f"{rep.duplicates} repeated closures dropped)")
    for s in rep.empty:
        print(f"  no closure list: {s}")
    for s in rep.problems:
        print(f"  could not read: {s}")
    run_event_closures()
    return run_geocode_closures([])


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
        elif source == "recurring_events":
            n = run_recurring()
        elif source == "event_closures":
            n = run_event_closures()
        elif source == "visitphilly":
            n = run_visitphilly()
        elif source == "ticketmaster":
            n = run_ticketmaster()
        elif source == "press_releases":
            if "--saved" not in argv or argv.index("--saved") == len(argv) - 1:
                raise SystemExit("press_releases needs --saved <files or folders of notice pages saved by hand>")
            n = run_press_releases(argv[argv.index("--saved") + 1:])
            if n is None:
                print(f"{source}: no new or edited notices, nothing loaded")
                return
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
