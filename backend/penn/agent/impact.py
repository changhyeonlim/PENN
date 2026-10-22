"""Impact tiers: which records matter to someone deciding whether and how to go somewhere.

"Rittenhouse next week" returns about 90 records; a person planning a trip cares about a handful:
what is happening there, and what changes how they get in. This sorts records into three tiers,
in plain code so the rules are testable and the model cannot drop them:

    events          things to go to: visitphilly and ticketmaster listings, curated events
    getting_around  closures that change a drive in: announced (timed) street closures, a Full
                    Closure on a collector or bigger street (class 1-4), a short Partial Closure
                    (14 days or less) on an arterial (class 1-3). Split into new (starts inside the
                    window) and ongoing (started before it). Two more lines when they apply:
                    expected, a parade or street fair listed with no City closure notice that day (its
                    street may be blocked for a while), and lane_work, the main streets with weeks-long
                    lane closures (one line, not one per permit).
    minor           everything else, returned only as counts by plain-language kind: sidewalk work,
                    parking and loading zones, lane work on side streets, planned work, and work on
                    streets just outside the neighborhood asked about

For a neighborhood, a record counts only on the segments inside its outline (locate.area_inside): the
area's r9 cells reach a block or two past it.

Closures that belong together become one group: permits of one kind with the same dates (eight
block-party permits closing Walnut St for Open Streets), or announced closures from one notice on
one day ("Philly Bike Ride: ..."). A group cites its first record; the API highlights all of them.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime

import h3

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
    "outside": "work on streets just outside the area",
    "other": "other permits",
}
LANE_WORK_STREETS = 4     # lane_work names at most this many main streets, the most permits first

# What a listed event does to the street it is on, when no City notice covers that day. A parade or street fair
# takes the street itself ("roadblocks"); any other dated listing, a show or a festival, brings crowds and
# Uber/Lyft cars stopping at the curb ("crowds"). "Festival" alone is often indoors, so it is not a street event;
# a listing whose address is blocks of a street ("6800 & 6900 blocks of Torresdale Avenue") is one.
STREET_EVENT = re.compile(r"\b(parade|procession|street (fair|festival|party)|block party|5k|10k|fun run|open streets)\b",
                          re.I)
ON_THE_STREET = re.compile(r"\bblocks? of\b|\bbetween\b", re.I)
ADDRESS_STREET = re.compile(r"\b\d+[A-Z]?(?:\s*&\s*\d+)?\s+(?:blocks? of\s+)?(?:([NSEW])\.?\s+)?([A-Za-z0-9]+(?: [A-Za-z0-9]+)*?)\s+"
                            r"(street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|parkway|pkwy|lane|ln|place|pl)\b",
                            re.I)
SUFFIX = {"street": "St", "avenue": "Ave", "road": "Rd", "boulevard": "Blvd", "drive": "Dr", "parkway": "Pkwy",
          "lane": "Ln", "place": "Pl"}
CROWD_MAX_DAYS = 31       # a months-long exhibition is not a crowd on one day; a three-week Halloween run is
CROWD_STREETS = 3         # crowds name at most this many streets, the most listings first


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


def _clip(r: dict, inside: frozenset[int] | None) -> dict | None:
    """The record kept to the segments whose middle lies inside the area's outline; None when every
    segment is outside. Records with no segments (listings, unplaced closures) stay as they are."""
    if not inside or not r.get("seg_ids"):
        return r
    from ..geo import intersections
    from ..geo.locate import INSIDE_RES
    by_id = intersections.network().by_id

    def mid_inside(i: int) -> bool:
        s = by_id.get(i)
        if s is None or not s.coords:
            return True                       # unknown segment: keep it rather than lose the record
        (x0, y0), (x1, y1) = s.coords[0], s.coords[-1]
        return h3.str_to_int(h3.latlng_to_cell((y0 + y1) / 2, (x0 + x1) / 2, INSIDE_RES)) in inside

    keep = [i for i in r["seg_ids"] if mid_inside(i)]
    return r | {"seg_ids": keep} if keep else None


def _lane_work(rows: list[dict]) -> dict | None:
    """Weeks-long lane closures on main streets as one line: which streets and blocks, citing each permit.
    Each alone is background; together they say "expect a lane down on Poplar St"."""
    if not rows:
        return None
    from ..geo import intersections
    by_id = intersections.network().by_id
    per_street: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        for name in {_pretty(str(by_id[i].street)) for i in r.get("seg_ids") or []
                     if i in by_id and 1 <= by_id[i].cls <= MAJOR_FOR_PARTIAL}:
            per_street[name].append(r)
    top = sorted(per_street, key=lambda n: -len(per_street[n]))[:LANE_WORK_STREETS]
    picked = list({r["event_id"]: r for n in top for r in per_street[n]}.values())
    segs = [i for r in picked for i in r.get("seg_ids") or []
            if i in by_id and _pretty(str(by_id[i].street)) in top]
    ids = [r["event_id"] for r in picked]
    return {"kind": "lane closures lasting weeks (Partial Closure: the street stays open)",
            "streets": streets_text(segs), "titles": sorted({r.get("title", "") for r in picked}),
            "more_streets": max(0, len(per_street) - len(top)), "event_ids": ids,
            "cite": " ".join(f"[id:{i}]" for i in ids)}


def event_street(description: str) -> str | None:
    """'Fairmount Ave' from '2333 Fairmount Avenue, Philadelphia, PA' or 'Union Transfer, 1026 Spring Garden
    Street, ...'; None when the listing gives no street address."""
    m = ADDRESS_STREET.search(description or "")
    if not m:
        return None
    direction, name, suffix = m.groups()
    return " ".join(filter(None, [direction and direction.upper(), *(w.capitalize() for w in name.split()),
                                  SUFFIX.get(suffix.lower(), suffix.capitalize())]))


def _expected(events: list[dict], announced: list[dict]) -> list[dict]:
    """What listed events do to their streets when no City closure notice covers that day (an event with a
    notice is already in getting_around as announced closures): roadblocks for a parade or street fair, one
    item each; crowds and rideshare stops for any other dated listing, one item per street."""
    noticed = {_span(r)[0] for r in announced if r.get("source") == "event_closures"}
    roadblocks, crowds = [], defaultdict(list)
    for e in events:
        street = event_street(e.get("description") or "")
        s, end = _span(e)
        if street is None or s in noticed:
            continue
        if STREET_EVENT.search(e.get("title") or "") or ON_THE_STREET.search(e.get("description") or ""):
            times = (f", {_clock(e['start_ts'])}" + (f" – {_clock(e['end_ts'])}" if e.get("end_ts") else "")
                     if e.get("start_ts") else "")
            roadblocks.append({"impact": "roadblocks", "event_id": e["event_id"], "title": e.get("title"),
                               "street": street, "when": when(e) + times,
                               "note": f"an event listing, not a City closure notice: expect temporary roadblocks "
                                       f"on {street} around it, not a confirmed closure",
                               "cite": f"[id:{e['event_id']}]"})
        elif s is not None and end is not None and (end - s).days < CROWD_MAX_DAYS:
            crowds[street].append(e)
    out = roadblocks
    for street in sorted(crowds, key=lambda st: -len(crowds[st]))[:CROWD_STREETS]:
        ids = [e["event_id"] for e in crowds[street]]
        out.append({"impact": "crowds", "street": street, "titles": [e.get("title") for e in crowds[street]],
                    "note": f"listed events: expect crowds and Uber/Lyft cars stopping on {street} around their times",
                    "event_ids": ids, "cite": " ".join(f"[id:{i}]" for i in ids)})
    return out


def brief(rows: list[dict], start: str, inside: frozenset[int] | None = None) -> dict:
    """Sort search_events rows (with seg_ids) into the trip brief. `start` is the window start (ISO);
    `inside` a neighborhood's outline (locate.area_inside), None for a corner, address or pin."""
    w0 = datetime.fromisoformat(start).astimezone(LOCAL_TZ).date()
    events, new, ongoing, long_lane = [], [], [], []
    minor: dict[str, int] = defaultdict(int)
    for r in rows:
        r = _clip(r, inside)
        if r is None:
            minor["outside"] += 1
            continue
        t = tier(r, _seg_class(r.get("seg_ids") or []))
        if t == "event":
            events.append(r)
        elif t == "getting_around":
            s, _ = _span(r)
            (new if s is None or s >= w0 else ongoing).append(_mark_main(r))
        elif t == "long_lane":
            long_lane.append(r)
        else:
            minor[t] += 1
    getting_around = {"expected": _expected(events, new + ongoing),
                      "new": sorted(_grouped(new), key=_weight), "ongoing": sorted(_grouped(ongoing), key=_weight),
                      "lane_work": _lane_work(long_lane)}
    by_kind = {MINOR_KINDS[k]: n for k, n in sorted(minor.items(), key=lambda kv: -kv[1]) if n}
    return {
        "detail": "brief",
        "events": [_compact(r) for r in events],
        "getting_around": {k: v for k, v in getting_around.items() if v or k in ("new", "ongoing")},
        "minor": {"count": sum(by_kind.values()), "by_kind": by_kind},
    }
