"""Impact tiers: which records matter to someone deciding whether and how to go somewhere.

"Rittenhouse next week" returns about 90 records; a person planning a trip cares about a handful:
what is happening there, and what changes how they get in. This sorts records into three tiers,
in plain code so the rules are testable and the model cannot drop them:

    events          things to go to: visitphilly and ticketmaster listings, curated events
    getting_around  closures that change a drive in: announced (timed) street closures, a Full
                    Closure on a collector or bigger street (class 1-4), a short Partial Closure
                    (14 days or less) on an arterial (class 1-3). Split into new (starts inside the
                    window) and ongoing (started before it).
    minor           everything else, returned only as counts by plain-language kind: sidewalk work,
                    parking and loading zones, lane work on side streets, long lane work, planned work

Closures that belong together become one group: permits of one kind with the same dates (eight
block-party permits closing Walnut St for Open Streets), or announced closures from one notice on
one day ("Philly Bike Ride: ..."). A group cites its first record; the API highlights all of them.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime

from ..config import LOCAL_TZ

EVENT_SOURCES = ("visitphilly", "ticketmaster")
MAJOR_FOR_FULL = 4        # a Full Closure on class 1-4 (expressway to collector) changes a drive
MAJOR_FOR_PARTIAL = 3     # a lane closure matters only on an arterial (class 1-3)
SHORT_PARTIAL_DAYS = 14   # and only when it is temporary; months-long lane work is background
DESCRIPTION_CHARS = 200

MINOR_KINDS = {
    "footway": "sidewalk work",
    "parking": "parking and loading zones",
    "side_lane": "lane work on side streets",
    "long_lane": "longer-running lane work on main streets",
    "side_closure": "side-street closures",
    "block_party": "block parties on side streets",
    "planned": "planned or open-ended work with no firm dates",
    "other": "other permits",
}


def _seg_class(seg_ids: list[int]) -> int | None:
    """The most major centerline class among the record's segments; None when unknown."""
    if not seg_ids:
        return None
    from ..geo import intersections
    by_id = intersections.network().by_id
    classes = [by_id[i].cls for i in seg_ids if i in by_id and 1 <= by_id[i].cls <= 5]
    return min(classes) if classes else None


def _day(v: str | None) -> date | None:
    if not v:
        return None
    return datetime.fromisoformat(v).astimezone(LOCAL_TZ).date() if "T" in v else date.fromisoformat(v)


def _span(r: dict) -> tuple[date | None, date | None]:
    return _day(r.get("start_date") or r.get("start_ts")), _day(r.get("end_date") or r.get("end_ts"))


def tier(r: dict, cls: int | None) -> str:
    """'event', 'getting_around' or a minor kind (a key of MINOR_KINDS)."""
    if r.get("source") in EVENT_SOURCES or (r.get("event_type") == "event" and r.get("source") != "event_closures"):
        return "event"
    if r.get("source") == "event_closures":
        return "getting_around"
    if r.get("claim_certainty") == "planned" or r.get("open_ended"):
        return "planned"
    occ = r.get("occupancy") or ""
    if occ == "Footway":
        return "footway"
    if r.get("event_type") == "parking_restriction":
        return "parking"
    if occ == "Full Closure":
        if cls is None or cls <= MAJOR_FOR_FULL:
            return "getting_around"
        return "block_party" if r.get("event_type") == "block_party" else "side_closure"
    if occ == "Partial Closure":
        if cls is not None and cls <= MAJOR_FOR_PARTIAL:
            s, e = _span(r)
            short = s is not None and e is not None and (e - s).days <= SHORT_PARTIAL_DAYS
            return "getting_around" if short else "long_lane"
        return "side_lane"
    return "other"


def _pretty(street: str) -> str:
    """'S 18TH ST' -> 'S 18th St'."""
    out = []
    for w in street.split():
        if w in ("N", "S", "E", "W"):
            out.append(w)
        elif re.fullmatch(r"\d+(ST|ND|RD|TH)", w):
            out.append(w.lower())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def _blocks(nums: list[int]) -> str:
    nums = sorted(set(nums))
    if len(nums) == 1:
        return f"{nums[0]} block"
    if nums == list(range(nums[0], nums[-1] + 100, 100)):
        return f"{nums[0]}–{nums[-1]} blocks"
    return ", ".join(str(n) for n in nums) + " blocks"


def streets_text(seg_ids: list[int]) -> str:
    """'Walnut St (1400–1900 blocks), S 18th St (100–200 blocks)' from centerline segments."""
    if not seg_ids:
        return ""
    from ..geo import intersections
    by_id = intersections.network().by_id
    blocks: dict[str, list[int]] = defaultdict(list)
    for i in seg_ids:
        s = by_id.get(i)
        if s is None:
            continue
        name = _pretty(str(s.street))
        blocks[name]
        if s.addr_lo:
            blocks[name].append(s.addr_lo // 100 * 100)
    return ", ".join(f"{n} ({_blocks(b)})" if b else n for n, b in blocks.items())


def _clock(v: str) -> str:
    t = datetime.fromisoformat(v).astimezone(LOCAL_TZ)
    return f"{t:%I:%M %p}".lstrip("0")


def when(r: dict) -> str:
    """'Sun Oct 25', 'Tue Oct 20 – Mon Oct 26' or 'Sat Oct 17, 6:30 AM – 12:00 PM': weekdays computed
    here, because the model gets them wrong ("Saturday, October 25" for a Sunday)."""
    s, e = _span(r)
    if s is None:
        return ""
    day = lambda d: f"{d:%a %b} {d.day}"
    if r.get("start_ts") and not r.get("start_date"):
        times = _clock(r["start_ts"]) + (f" – {_clock(r['end_ts'])}" if r.get("end_ts") else "")
        return f"{day(s)}, {times}" if e in (None, s) else f"{day(s)} {_clock(r['start_ts'])} – {day(e)} {_clock(r['end_ts'])}"
    if e is None:
        return f"from {day(s)}, no end date listed"
    return day(s) if e == s else f"{day(s)} – {day(e)}"


def _compact(r: dict) -> dict:
    """What the model needs for one record in a brief: no shape, no seg_ids, a short description."""
    out = {k: v for k, v in r.items() if k not in ("seg_ids", "geometry_wkt") and v not in (None, "", [])}
    out["when"] = when(r)
    if len(out.get("description") or "") > DESCRIPTION_CHARS:
        out["description"] = out["description"][:DESCRIPTION_CHARS].rstrip() + "…"
    if streets := streets_text(r.get("seg_ids") or []):
        out["streets"] = streets
    return out


def _group_key(r: dict) -> tuple | None:
    """Records that tell one story: announced closures from one notice on one day, or permits of
    one kind with the same dates. None when a record stands alone."""
    if r.get("source") == "event_closures":
        notice = (r.get("title") or "").split(":", 1)[0].strip()
        return ("notice", notice, _span(r)[0]) if notice else None
    if r.get("start_date") and r.get("end_date"):
        return ("permits", r.get("event_type"), r.get("occupancy"), r["start_date"], r["end_date"])
    return None


def _group(members: list[dict]) -> dict:
    first = members[0]
    ids = [m["event_id"] for m in members]
    if first.get("source") == "event_closures":
        notice = first["title"].split(":", 1)[0].strip()
        return {"main_street": any(m.get("main_street") for m in members), "group": notice,
                "kind": "announced street closures", "event_ids": ids, "cite": f"[id:{ids[0]}]",
                "organization": first.get("organization"),
                "closures": [{"event_id": m["event_id"],
                              "where": m["title"].split(":", 1)[1].strip() if ":" in m["title"] else m["title"],
                              "when": when(m)} for m in members]}
    seg_ids = [i for m in members for i in (m.get("seg_ids") or [])]
    return {"main_street": any(m.get("main_street") for m in members), "group": f"{len(members)} {first.get('event_type', 'permit').replace('_', ' ')} permits, same dates",
            "kind": first.get("event_type"), "occupancy": first.get("occupancy"), "when": when(first),
            "streets": streets_text(seg_ids), "titles": sorted({m.get("title", "") for m in members}),
            "event_ids": ids, "cite": f"[id:{ids[0]}]"}


def _grouped(rows: list[dict]) -> list[dict]:
    """Groups for keys shared by two or more records, compact singles otherwise, in input order."""
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if (k := _group_key(r)) is not None:
            by_key[k].append(r)
    out, done = [], set()
    for r in rows:
        k = _group_key(r)
        if k is not None and len(by_key[k]) > 1:
            if k not in done:
                done.add(k)
                out.append(_group(by_key[k]))
        else:
            out.append(_compact(r))
    return out


def _weight(item: dict) -> tuple:
    """Biggest disruption first: groups by size, then main streets, so the verdict line can take item 0."""
    size = len(item.get("event_ids") or [item.get("event_id")])
    return (-size, not item.get("main_street"))


def _mark_main(r: dict) -> dict:
    cls = _seg_class(r.get("seg_ids") or [])
    return r | {"main_street": cls is not None and cls <= MAJOR_FOR_PARTIAL}


def brief(rows: list[dict], start: str) -> dict:
    """Sort search_events rows (with seg_ids) into the trip brief. `start` is the window start (ISO)."""
    w0 = datetime.fromisoformat(start).astimezone(LOCAL_TZ).date()
    events, new, ongoing = [], [], []
    minor: dict[str, int] = defaultdict(int)
    for r in rows:
        t = tier(r, _seg_class(r.get("seg_ids") or []))
        if t == "event":
            events.append(r)
        elif t == "getting_around":
            s, _ = _span(r)
            (new if s is None or s >= w0 else ongoing).append(_mark_main(r))
        else:
            minor[MINOR_KINDS[t]] += 1
    return {
        "detail": "brief",
        "events": [_compact(r) for r in events],
        "getting_around": {"new": sorted(_grouped(new), key=_weight), "ongoing": sorted(_grouped(ongoing), key=_weight)},
        "minor": {"count": sum(minor.values()), "by_kind": dict(sorted(minor.items(), key=lambda kv: -kv[1]))},
    }
