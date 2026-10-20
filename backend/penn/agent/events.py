"""Events by name (p5-08): "When is Rocky Run and where are the road closures?"

A question that names an event needs no place. Code finds the event in data/event_series.csv (names
and aliases), reads every past edition from the City's closure notices city-wide, works out the next
date from past dates, and sums up the route. The model gets one handle and these facts; it never
guesses a date or a street. Where PENN has no record, the caller (find_event in context.py) adds web
results (penn/web/tavily.py).

Pure functions take rows, so they test without ClickHouse; series_history runs the query.
"""
from __future__ import annotations

import calendar
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .. import db
from ..config import LOCAL_TZ
from ..geo import locate
from ..tools import _jsonable, history

# Words that say what kind of event it is, not which one: "parade" alone must not pick a parade.
GENERIC = set("""the a an of and at in on for to annual philadelphia philly city parade festival fest run race
    5k 10k march rally concert celebration day week weekend event events walk party block""".split())
MAX_CLOSURES = 12


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower().replace("'", "")).strip() + " "


def _words(text: str) -> set[str]:
    return {w for w in _norm(text).split() if len(w) > 1}


def match_event(text: str, cat: dict[str, dict] | None = None) -> list[str]:
    """Series the text names: an alias or the full name as a phrase first; else the series whose
    distinctive name words (GENERIC removed) mostly appear. Several when they tie."""
    cat = cat if cat is not None else history.catalog()
    t = _norm(text)
    exact = [s for s, r in cat.items()
             if any(_norm(a) in t for a in [r.get("name", "")] + (r.get("aliases") or "").split("|") if a.strip())]
    if exact:
        # The longest phrase wins: "philadelphia marathon" over "the marathon" when both name a series.
        def longest(s):
            r = cat[s]
            return max(len(a) for a in [r.get("name", "")] + (r.get("aliases") or "").split("|") if a.strip() and _norm(a) in t)
        best = max(longest(s) for s in exact)
        return sorted(s for s in exact if longest(s) == best)
    words = _words(text)
    scored = []
    for s, r in cat.items():
        full = _words(r.get("name", ""))
        name, kind = full - GENERIC, full & GENERIC
        if not name:
            continue
        share = len(name & words) / len(name)
        # A loose match also needs one of the event's kind words: "parade on Broad Street" names a
        # street, not the Broad Street Run.
        if share >= 0.6 and (not kind or kind & words):
            scored.append((share, s))
    if not scored:
        return []
    top = max(sc for sc, _ in scored)
    return sorted(s for sc, s in scored if sc == top)


def short_name(series: str, text: str, cat: dict[str, dict] | None = None) -> str:
    """What people call the event, for a web search: the alias the text used (the longest), else the
    first alias, else the catalog name. "Rocky Run" finds the organizer's page; the catalog's "Rocky Run
    5K / 10 Mile Run/Walk" found only last year's City notices."""
    cat = cat if cat is not None else history.catalog()
    r = cat.get(series, {})
    aliases = [a.strip() for a in (r.get("aliases") or "").split("|") if a.strip()]
    t = _norm(text)
    used = sorted((a for a in aliases if _norm(a) in t), key=len, reverse=True)
    pick = used[0] if used else (aliases[0] if aliases else r.get("name") or series.replace("-", " "))
    return " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in pick.split())


EDITIONS_SQL = """
    SELECT c.closure_id AS closure_id, c.year AS year, c.start_date AS date, c.street AS street,
           c.from_street AS from_street, c.to_street AS to_street, c.start_ts AS start, c.end_ts AS end,
           c.conditional AS conditional, c.source_url AS source_url, c.geocoded AS geocoded,
           e.event_id AS event_id, e.h3_cells AS cells
    FROM (SELECT * FROM event_closures FINAL WHERE series = %(series)s) AS c
    LEFT JOIN (SELECT event_id, source_record_id, h3_cells FROM events FINAL WHERE source = 'event_closures') AS e
      ON e.source_record_id = c.closure_id
    ORDER BY c.start_date, c.start_ts, c.street
"""


def _clock(iso: str | None) -> str | None:
    if not iso:
        return None
    t = datetime.fromisoformat(iso).astimezone(LOCAL_TZ)
    return t.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")


def _day_label(d: date) -> str:
    return f"{d:%A, %B} {d.day}, {d.year}"


def neighborhoods(cells: set[int], areas: dict[str, list[int]] | None = None, min_cells: int = 2) -> list[str]:
    """Neighborhoods the closures run through, most cells first. Multi-neighborhood areas ("center
    city") are left out: they would only repeat their members."""
    areas = areas if areas is not None else locate.areas()
    aliases = _alias_names()
    hits = Counter({name: len(cells & set(c)) for name, c in areas.items() if name not in aliases})
    return [n.title() for n, k in hits.most_common() if k >= min_cells]


def _alias_names() -> set[str]:
    path = locate.AREAS_DIR / "aliases.json"
    if not path.exists():
        return set()
    return {k.lower() for k in json.loads(path.read_text()) if not k.startswith("_")}


def editions(rows: list[dict], areas: dict[str, list[int]] | None = None) -> list[dict]:
    """rows from EDITIONS_SQL (times as local ISO strings) -> one entry per year: the main day (the
    date with most closures, race day not set-up), hours, closures, a ready cite, neighborhoods."""
    by_year: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_year[int(r["year"])].append(r)
    out = []
    for year, rs in sorted(by_year.items()):
        main = Counter(r["date"] for r in rs).most_common(1)[0][0]
        day_rows = [r for r in rs if r["date"] == main]
        placed = [r for r in rs if r.get("event_id")]
        cite_id = (placed[0]["event_id"] if placed else f"event_closures:{rs[0]['closure_id']}")
        starts = sorted(r["start"] for r in day_rows if r.get("start"))
        ends = sorted(r["end"] for r in day_rows if r.get("end"))
        cells = {c for r in rs for c in (r.get("cells") or [])}
        shown = day_rows[:MAX_CLOSURES]
        days = sorted({r["date"] for r in rs})
        out.append({
            "year": year,
            "date": main,
            "day": _day_label(date.fromisoformat(main)),
            **({"closure_days": [_day_label(date.fromisoformat(d)) for d in days]} if len(days) > 1 else {}),
            "closures_from": _clock(starts[0]) if starts else None,
            "reopen_by": _clock(ends[-1]) if ends else None,
            "cite": f"[id:{cite_id}]",
            "event_id": cite_id,              # citations.collect_ids reads event_id fields, not cite strings
            "event_ids": [r["event_id"] for r in placed],   # the whole route: find_event keeps it server side
            "source_url": rs[0].get("source_url") or "",
            "closures": [{k: r[k] for k in ("street", "from_street", "to_street")} | {"start": _clock(r.get("start")),
                          "end": _clock(r.get("end"))} for r in shown],
            **({"more_closures": len(day_rows) - len(shown)} if len(day_rows) > len(shown) else {}),
            "on_map": len(placed),
            "neighborhoods": neighborhoods(cells, areas) if cells else [],
            "_rows": rs,
        })
    return out


# Next date

def _nth(d: date) -> int:
    return (d.day - 1) // 7 + 1


def _is_last(d: date) -> bool:
    return d.day + 7 > calendar.monthrange(d.year, d.month)[1]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date | None:
    first = date(year, month, 1)
    d = first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
    return d if d.month == month else None


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def thanksgiving(year: int) -> date:
    return _nth_weekday(year, 11, 3, 4)


ORDINAL = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}
WEEKDAY = list(calendar.day_name)


def _rules(d: date) -> list[tuple[str, callable]]:
    """Date rules one past date satisfies, each as (description, year -> date)."""
    wd, m = d.weekday(), d.month
    month = calendar.month_name[m]
    rules = [(f"the {ORDINAL[_nth(d)]} {WEEKDAY[wd]} of {month}", lambda y, n=_nth(d): _nth_weekday(y, m, wd, n)),
             (f"{month} {d.day}", lambda y, day=d.day: date(y, m, day))]
    if _is_last(d):
        rules.append((f"the last {WEEKDAY[wd]} of {month}", lambda y: _last_weekday(y, m, wd)))
    off = (d - thanksgiving(d.year)).days
    if abs(off) <= 21:
        when = "Thanksgiving Day" if off == 0 else f"{abs(off)} days {'after' if off > 0 else 'before'} Thanksgiving"
        rules.append((when, lambda y, o=off: thanksgiving(y) + timedelta(days=o)))
    return rules


def next_date(dates: list[date], today: date) -> dict:
    """From past main dates: an announced date (on or after today) wins; otherwise every rule that
    fits more than one past year, with its support and the date it gives next. Rules that disagree
    are all returned, best supported first."""
    dates = sorted(set(dates))
    upcoming = [d for d in dates if d >= today]
    if upcoming:
        return {"announced": _day_label(upcoming[0]), "date": upcoming[0].isoformat(), "estimates": []}
    past = [d for d in dates if d < today]
    if not past:
        return {"announced": None, "estimates": []}
    support: dict[str, list[int]] = defaultdict(list)
    makers: dict[str, callable] = {}
    for d in past:
        for desc, make in _rules(d):
            support[desc].append(d.year)
            makers.setdefault(desc, make)
    # A rule must fit at least half the years (and two, when there are two): with nine Marathons,
    # "November 19" fits two by coincidence.
    need = max(min(2, len(past)), -(-len(past) // 2))
    estimates = []
    for desc, years in support.items():
        if len(years) < need:
            continue
        nxt = None
        for y in (today.year, today.year + 1):
            try:
                cand = makers[desc](y)
            except ValueError:
                cand = None
            if cand and cand >= today:
                nxt = cand
                break
        if nxt:
            estimates.append({"rule": desc, "next": _day_label(nxt), "date": nxt.isoformat(),
                              "support": f"{len(years)} of {len(past)} years ({', '.join(map(str, sorted(years)))})"})
    estimates.sort(key=lambda e: (-int(e["support"].split()[0]), e["date"]))
    # The same date from two descriptions (Nov 14 as "the second Saturday" and "November 14") is one estimate.
    seen, unique = set(), []
    for e in estimates:
        if e["date"] not in seen:
            seen.add(e["date"])
            unique.append(e)
    return {"announced": None, "estimates": unique,
            **({"note": "Past dates fit different rules; they give different dates."} if len(unique) > 1 else {})}


_ON_PAGE = re.compile(r"\b(january|february|march|april|may|june|july|august|september|october|november|december) "
                      r"(\d{1,2})\b(?:\s+(\d{4}))?")


def confirm_estimates(nxt: dict, pages: list[dict], name: str) -> None:
    """Mark each estimate a web page about the event states: the page names the event and gives the
    estimate's month and day, with its year or no year ("November 14, Philadelphia" on the organizer's
    page). A page giving the date with another year ("November 14, 2025") does not count. Adds
    confirmed_by: [{"cite", "site"}] to the estimate. When any is confirmed, only confirmed ones stay in
    nxt["estimates"] and the rest move to nxt["also_fits"]: told to lead with the confirmed date, the
    model still listed the dates in calendar order."""
    from .citations import _plain          # the same normalizing the web fact check uses
    want = _words(name) - GENERIC
    estimates = nxt.get("estimates", [])
    for est in estimates:
        d = date.fromisoformat(est["date"])
        month, day = calendar.month_name[d.month].lower(), d.day
        hits = []
        for p in pages:
            text = _plain(f"{p.get('title', '')} {p.get('text') or p.get('excerpt') or ''}")
            if want and not want <= set(text.split()):
                continue
            if any(m.group(1) == month and int(m.group(2)) == day and (not m.group(3) or int(m.group(3)) == d.year)
                   for m in _ON_PAGE.finditer(text)):
                hits.append({"cite": f"[id:{p['event_id']}]", "site": p.get("site", "")})
        if hits:
            est["confirmed_by"] = hits[:2]
    confirmed = [e for e in estimates if "confirmed_by" in e]
    if confirmed and len(confirmed) < len(estimates):
        nxt["also_fits"] = [{k: e[k] for k in ("rule", "next", "cites") if k in e} for e in estimates if e not in confirmed]
        nxt["estimates"] = confirmed
        nxt.pop("note", None)


def route_summary(edition: dict) -> dict:
    """Start and finish (the closures that open first and reopen last on the main day), the streets
    closed most often, and the neighborhoods crossed."""
    rows = [r for r in edition["_rows"] if r["date"] == edition["date"]]
    timed = [r for r in rows if r.get("start")]
    first = min(timed, key=lambda r: r["start"]) if timed else None
    last = max((r for r in rows if r.get("end")), key=lambda r: r["end"], default=None)
    streets = Counter(r["street"] for r in rows)
    fmt = lambda r: f"{r['street']}" + (f" from {r['from_street']} to {r['to_street']}" if r.get("from_street") else "")
    return {
        "closes_first": fmt(first) + f" ({_clock(first['start'])})" if first else None,
        "reopens_last": fmt(last) + f" ({_clock(last['end'])})" if last else None,
        "main_streets": [s for s, _ in streets.most_common(6)],
        "neighborhoods": edition["neighborhoods"],
    }


def series_history(series: str, today: date) -> dict:
    """Everything PENN has on one event, city-wide: past editions, the next date, the latest route."""
    info = history.catalog().get(series, {})
    rows = _jsonable(db.query(EDITIONS_SQL, {"series": series}))
    eds = editions(rows)
    nxt = next_date([date.fromisoformat(e["date"]) for e in eds], today)
    # Each estimate carries the cites of the years it rests on, ready to copy after the estimate.
    by_year = {e["year"]: e["cite"] for e in eds}
    for est in nxt.get("estimates", []):
        years = [int(y) for y in re.findall(r"\b(\d{4})\b", est["support"].split("(", 1)[-1])]
        est["cites"] = " ".join(by_year[y] for y in years if y in by_year)
    latest = eds[-1] if eds else None
    return {
        "series": series,
        "name": info.get("name") or series.replace("-", " ").title(),
        "recurrence": info.get("recurrence") or None,
        "editions": [{k: v for k, v in e.items() if k != "_rows"} for e in eds],
        "next": nxt,
        "latest_route": route_summary(latest) | {"year": latest["year"]} if latest else None,
        "coverage": "Only editions whose City closure notices PENN has collected. A year missing may be a gap in "
                    "PENN's records, not a year without the event.",
    }
