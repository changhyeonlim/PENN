"""get_location_history: past event closures at a place, grouped by event and year.

This is PENN's temporal tool. It answers "was this street closed for X in past years?" and
"what usually closes here?" from the City's announced closures, so:

- An annual event gives a pattern ("closed here for the Thanksgiving parade in 2018, 2019,
  2021-2025"), never a promise about this year.
- A one-off event (a championship parade, the NFL Draft) gives no "same event next year";
  it shows that big events close this place.
- Every answer carries what the records cover, because a year missing from a series can be a
  gap in PENN's records, not a year without closures.

The grouping is a pure function so it is testable without ClickHouse; the query lives in
penn.tools.get_location_history.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

CATALOG = Path(__file__).resolve().parents[3] / "data" / "event_series.csv"
MAX_CLOSURES_PER_EDITION = 8

COVERAGE_NOTE = ("Only events whose City closure notices PENN has collected, and only closures PENN could "
                 "place on the street map. A year missing from an event can be a gap in PENN's records, "
                 "not a year without closures.")


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict]:
    """series -> {name, category, recurrence}. Missing file or series = unknown, never guessed."""
    if not CATALOG.exists():
        return {}
    with open(CATALOG, encoding="utf-8") as f:
        return {r["series"]: r for r in csv.DictReader(f)}


MAX_DIGEST_EVENTS = 10


def digest(rows: list[dict], cat: dict[str, dict]) -> dict:
    """The short form of the history that search_events carries: per event, one record per year."""
    # Per series and year, the date with the most closures here (race day, not Friday set-up).
    per_day: dict[tuple[str, int, str], list[dict]] = defaultdict(list)
    for r in rows:
        per_day[(r["series"], r["year"], r["date"])].append(r)
    by_series: dict[str, dict[int, dict]] = defaultdict(dict)
    for (s, y, d), lst in sorted(per_day.items(), key=lambda kv: (-len(kv[1]), kv[0][2])):
        by_series[s].setdefault(y, {"year": y, "date": d, "event_id": lst[0]["event_id"]})
    events = []
    for s, years in sorted(by_series.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:MAX_DIGEST_EVENTS]:
        info = cat.get(s, {})
        events.append({"name": info.get("name") or s.replace("-", " ").title(),
                       "recurrence": info.get("recurrence") or None,
                       "years": [years[y] for y in sorted(years)]})
    return {"events": events,
            "note": "Past event closures at this place (one record per year; cite its event_id). "
                    "A year missing may be a gap in PENN's records. Call get_location_history for streets and times."}


SAME_DATES_SLACK = 3        # an annual event moves a few days a year to stay on its weekday
SAME_DATES_MAX_DAYS = 31    # beyond a month, "the same dates" stops meaning anything


def _shift(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:                                       # Feb 29
        return d.replace(year=d.year + years, day=28)


def same_dates(summary: dict, start: date, last: date) -> list[dict]:
    """Past editions of events at this place that fell on the window's dates in earlier years.

    summary is get_location_history's result; [start, last] are the window's calendar dates.
    An event that already has an edition inside the window is left out: it is announced, so it
    belongs in the current answer, not in a pattern.
    """
    if (last - start).days >= SAME_DATES_MAX_DAYS:
        return []
    slack = timedelta(days=SAME_DATES_SLACK)
    out = []
    for s in summary.get("events", []):
        years: dict[int, dict] = {}
        announced = False
        for e in s["editions"]:
            d = date.fromisoformat(e["date"])
            if start <= d <= last:
                announced = True
                break
            if d >= start:
                continue
            if d.year in (start.year, last.year):
                if d >= start - slack:   # this year's edition, just before the window: it already happened
                    announced = True
                    break
                continue
            for delta in {start.year - d.year, last.year - d.year}:
                if _shift(start, -delta) - slack <= d <= _shift(last, -delta) + slack:
                    years.setdefault(d.year, {"year": d.year, "date": e["date"], "cite": e["cite"]})
        if years and not announced:
            out.append({"name": s["name"], "recurrence": s["recurrence"], "years": [years[y] for y in sorted(years)]})
    return out


def same_dates_text(matches: list[dict], year: int) -> str:
    """The fixed, cited paragraph the API puts under a future-dated answer."""
    if not matches:
        return ""
    lines = []
    for m in matches:
        kind = f" ({m['recurrence']})" if m["recurrence"] else ""
        dates = "; ".join(f"{date.fromisoformat(y['date']):%b} {date.fromisoformat(y['date']).day}, {y['year']} "
                          f"{y['cite']}" for y in m["years"])
        lines.append(f"- {m['name']}{kind}: {dates}")
    return ("In past years, these events closed streets here on the same dates:\n" + "\n".join(lines) +
            f"\nThis is a pattern from past years, not a schedule: PENN has no notice for them in {year} yet.")


def summarize(rows: list[dict], cat: dict[str, dict], coverage: dict) -> dict:
    """rows: one per closure at the place, with event_id, series, year, date, street, from_street,
    to_street, start, end, conditional (times already local ISO strings)."""
    by_series: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_series[r["series"]][r["date"]].append(r)

    series_out = []
    for s, editions in sorted(by_series.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        info = cat.get(s, {})
        eds = []
        for d, closures in sorted(editions.items()):
            shown = closures[:MAX_CLOSURES_PER_EDITION]
            eds.append({
                "year": closures[0]["year"], "date": d,
                # One ready-made citation per edition: copying it beats rebuilding an ID by pattern.
                "cite": f"[id:{closures[0]['event_id']}]",
                # Every closure of the edition here: citing the edition lights them all on the map
                # (citations.expand_groups), not only the one its cite names.
                **({"event_ids": [c["event_id"] for c in closures]} if len(closures) > 1 else {}),
                "closures": [{k: c[k] for k in ("event_id", "street", "from_street", "to_street", "start", "end",
                                                "conditional")} for c in shown],
                **({"more_closures": len(closures) - len(shown)} if len(closures) > len(shown) else {}),
            })
        years = sorted({e["year"] for e in eds})
        series_out.append({
            "series": s,
            "name": info.get("name") or s.replace("-", " ").title(),
            "category": info.get("category") or None,
            "recurrence": info.get("recurrence") or None,     # annual | irregular | one_off | None = unknown
            "years_seen_here": years,
            "editions": eds,
        })

    years_all = sorted({y for s in series_out for y in s["years_seen_here"]})
    return {
        "summary": {
            "events_here": len(series_out),
            "editions_here": sum(len(s["editions"]) for s in series_out),
            "years_here": years_all,
            "by_category": dict(Counter(s["category"] or "unknown" for s in series_out)),
            "by_recurrence": dict(Counter(s["recurrence"] or "unknown" for s in series_out)),
        },
        "events": series_out,
        "coverage": {**coverage, "note": COVERAGE_NOTE},
    }
