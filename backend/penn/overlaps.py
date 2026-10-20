"""The same event listed twice: a visitphilly pick that Ticketmaster also sells tickets for.

A pair matches when all three hold:
- same day: the Ticketmaster date falls within the visitphilly item's dates (a run of a show matches
  each performance);
- same venue: the two points are within SAME_VENUE_M metres, or both name the same venue and are within
  SAME_NAMED_VENUE_M (the sources pin a stadium or arena at different spots: visitphilly and Ticketmaster
  put Xfinity Mobile Arena about 400 m apart);
- same event: the titles share at least half of the shorter title's distinctive words (venue names
  and filler words removed), or, for games, both name the same home team.

Matches link the two records; neither is deleted. The map draws one flag for a pair and its panel
names both listers.
"""
from __future__ import annotations

import math
import re
from datetime import date

SAME_VENUE_M = 200
SAME_NAMED_VENUE_M = 500
LISTER = "visitphilly"
SELLER = "ticketmaster"
# Philadelphia's home teams, as both sources write them. A game is a Ticketmaster "Sports" event.
TEAMS = ("eagles", "phillies", "flyers", "76ers", "sixers", "union", "wings")
STOP = set("""the a an and or at of in on with for to vs from by featuring feat presents present live tour show
    musical concert night philadelphia philly pa new edition""".split())


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in STOP}


def _venue(description: str) -> str:
    return (description or "").split(";")[0].split(",")[0]


def distinctive(title: str, description: str) -> set[str]:
    """Title words minus the venue's own words: 'X at Walnut Street Theatre' is about X."""
    return _words(title) - _words(_venue(description))


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def _day(v) -> date | None:
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def why_same(pick: dict, sale: dict) -> str | None:
    """The reason the two listings are one event, or None."""
    p0, p1, s0 = _day(pick.get("start_date")), _day(pick.get("end_date")), _day(sale.get("start_date"))
    if not (p0 and s0 and p0 <= s0 <= (p1 or p0)):
        return None
    m = metres(pick["centroid_lat"], pick["centroid_lon"], sale["centroid_lat"], sale["centroid_lon"])
    venue = _words(_venue(pick.get("description", "")))
    named = bool(venue) and venue == _words(_venue(sale.get("description", "")))
    if m > (SAME_NAMED_VENUE_M if named else SAME_VENUE_M):
        return None
    a, b = distinctive(pick["title"], pick.get("description", "")), distinctive(sale["title"], sale.get("description", ""))
    if a and b and len(a & b) >= max(1, math.ceil(min(len(a), len(b)) / 2)):
        return "same title"
    if "Sports" in (sale.get("description") or ""):
        team = next((t for t in TEAMS if t in _words(pick["title"]) and t in _words(sale["title"])), None)
        if team:
            return f"same team ({team})"
    return None


def same_as(rows: list[dict]) -> list[list[str]]:
    """[[visitphilly event_id, ticketmaster event_id, reason], ...] among the rows given."""
    picks = [r for r in rows if r.get("source") == LISTER and r.get("centroid_lat") is not None]
    sales = [r for r in rows if r.get("source") == SELLER and r.get("centroid_lat") is not None]
    return [[p["event_id"], s["event_id"], why] for p in picks for s in sales if (why := why_same(p, s))]


def covered(rows: list[dict]) -> list[list[str]]:
    """[[block party event_id, event closure event_id], ...]: a block party permit whose every street segment
    is closed that day by a City-announced event closure. It is one closure listed twice, the permit and the
    City's notice for the event it belongs to (OurFest's block party permits under the OurFest closures), so
    the map draws the announced closure, which has the event's name and hours, and folds the permit into it.
    The closure named is one that shares a segment with the permit."""
    by_day: dict[date, list[dict]] = {}
    for c in rows:
        if c.get("source") == "event_closures" and c.get("seg_ids") and (d := _day(c.get("start_date"))):
            by_day.setdefault(d, []).append(c)
    out = []
    for p in rows:
        if p.get("event_type") != "block_party" or not p.get("seg_ids"):
            continue
        p0 = _day(p.get("start_date"))
        p1 = _day(p.get("end_date")) or p0
        segs = set(p["seg_ids"])
        for d, closures in sorted(by_day.items()):
            if not (p0 and p0 <= d <= p1):
                continue
            if segs <= {s for c in closures for s in c["seg_ids"]}:
                out.append([p["event_id"], next(c["event_id"] for c in closures if segs & set(c["seg_ids"]))])
                break
    return out
