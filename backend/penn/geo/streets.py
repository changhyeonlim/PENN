"""Match street descriptions from closure notices to City street centerline segments.

Centerline: City of Philadelphia "Street Centerlines" (OpenDataPhilly), ArcGIS layer
Street_Centerline/FeatureServer/0. One LineString per block-length segment. Fields used:

    seg_id                       unique segment id; the same id as Lane Closures' seg_id
                                 (stored there as a string)
    pre_dir, st_name, st_type    name parts, e.g. "N", "BROAD", "ST"
    l_f_add, l_t_add,            address ranges on the left and right side; 0 = no address.
    r_f_add, r_t_add             Divided roads (the Parkway) number only one side per carriageway.

Facts confirmed while building this (Oct 2026):
- 41,252 segments, all LineString. fnode_/tnode_ exist but 20 nodes disagree with the
  coordinates, so intersections come from shared endpoint coordinates instead.
- No segment is named Eakins Oval, Binswanger Triangle, Navy Yard, Falls Bridge or I-676/I-76.
  Rows that depend on them stay unresolved: coordinates are never estimated.

Resolution kinds:
    from_to       "Broad Street" from "Olney Avenue" to "Windrim Avenue": the shortest chain of
                  Broad Street segments between the two intersections.
    block_range   "1600-2400 blocks of South Street": segments whose addresses overlap 1600-2499.
    named         a street or place with no from/to, resolved only if segments carry that name.
    one_end       from or to missing: unresolved.
    area          "All roads ...", lists, "All affected side streets": unresolved.
    route         curated legs from data/closure_routes.csv, for a closure whose notice has no
                  usable end ("Broad Street from Erie Avenue to the Navy Yard").
"""
from __future__ import annotations

import heapq
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, NamedTuple

import httpx

from .h3util import Coord

CENTERLINE_URL = (
    "https://services.arcgis.com/fLeGjb7u4uXqeF9q/arcgis/rest/services/"
    "Street_Centerline/FeatureServer/0/query"
)
CENTERLINE_FIELDS = "seg_id,pre_dir,st_name,st_type,suf_dir,l_f_add,l_t_add,r_f_add,r_t_add,class"
CENTERLINE_PAGE = 2000  # the layer's maxRecordCount

TYPES = {
    "STREET": "ST", "ST": "ST", "STREETS": "ST",
    "AVENUE": "AVE", "AVE": "AVE", "AV": "AVE", "AVENUES": "AVE",
    "BOULEVARD": "BLVD", "BLVD": "BLVD",
    "DRIVE": "DR", "DR": "DR", "DRIVES": "DR",
    "ROAD": "RD", "RD": "RD", "ROADS": "RD",
    "LANE": "LN", "LN": "LN",
    "PLACE": "PL", "PL": "PL",
    "SQUARE": "SQ", "SQ": "SQ",
    "PARKWAY": "PKWY", "PKWY": "PKWY",
    "CIRCLE": "CIR", "CIR": "CIR",
    "TERRACE": "TER", "TER": "TER",
    "TUNNEL": "TUNL", "TUNL": "TUNL",
    "BRIDGE": "BRG", "BRG": "BRG",
    "EXPRESSWAY": "EXPY", "EXPY": "EXPY",
    "COURT": "CT", "CT": "CT",
    "WAY": "WAY", "PIKE": "PIKE", "WALK": "WALK", "MALL": "MALL",
    "ALLEY": "ALY", "ALY": "ALY", "PLAZA": "PLZ", "PLZ": "PLZ",
}
PLURAL_TYPES = {"STREETS", "AVENUES", "DRIVES", "ROADS"}
DIRS = {"NORTH": "N", "N": "N", "SOUTH": "S", "S": "S", "EAST": "E", "E": "E", "WEST": "W", "W": "W"}
DIR_WORDS = {"N": "NORTH", "S": "SOUTH", "E": "EAST", "W": "WEST"}
# Applied to the space-separated upper-case name, longest first.
ALIASES = [
    ("MARTIN LUTHER KING JR", "MARTIN LUTHER KING"),
    ("BEN FRANKLIN", "BENJAMIN FRANKLIN"),
    ("JFK", "JOHN F KENNEDY"),
    ("MLK", "MARTIN LUTHER KING"),
]
# Lane and direction wording that describes which part of the road closes, not which road.
LEADING_NOTES = re.compile(r"^(?:(?:CENTER|INNER|OUTER) LANES OF|INBOUND|OUTBOUND)\s+")
TRAILING_NOTES = re.compile(r"\s+(?:INBOUND|OUTBOUND)(?:\s+LANES?)?$")
# Types under which an embedded "STREET" is part of the facility name ("Spring Garden Street
# Tunnel" is SPRING GARDEN TUNL in the centerline).
FACILITY_TYPES = {"TUNL", "BRG", "EXPY", "RAMP"}

DASH = "-–—�"  # hyphen, en dash, em dash, and U+FFFD where an en dash lost its encoding
BLOCK_RE = re.compile(
    rf"^(\d+)(?:\s*[{DASH}]\s*(\d+))?\s+(?:blocks?\s+of\s+)?(.+)$", re.IGNORECASE)
AREA_RE = re.compile(r"^(?:all\b|additional streets\b)|:", re.IGNORECASE)


class StreetName(NamedTuple):
    dir: str   # "N", "S", "E", "W" or "" when the notice does not say
    name: str  # "BROAD", "BENJAMIN FRANKLIN", "15TH"
    type: str  # "ST", "PKWY" ... or "" when the notice gives none

    @property
    def key(self) -> str:
        """Name compared without spaces, so "Waterworks" meets WATER WORKS."""
        name = self.name
        if self.type in FACILITY_TYPES:
            name = re.sub(r"\s+(?:STREET|ST)$", "", name)
        return name.replace(" ", "")

    def __str__(self) -> str:
        return " ".join(p for p in self if p)


def normalize(text: str) -> StreetName | None:
    """A notice's street wording as (dir, name, type). None if nothing name-like is left."""
    s = text.upper()
    s = re.sub(r"\([^)]*\)", " ", s)                 # "(north side)", "(all lanes)"
    s = re.sub(r"[.,'’�]", "", s)          # "John F. Kennedy", "d'Harnoncourt"
    s = re.sub(r"\s+", " ", s).strip()
    s = LEADING_NOTES.sub("", s)
    s = TRAILING_NOTES.sub("", s)
    for old, new in ALIASES:
        s = re.sub(rf"\b{old}\b", new, s)
    tokens = s.split()
    if not tokens:
        return None
    typ = ""
    if len(tokens) > 1 and tokens[-1] in TYPES:
        typ = TYPES[tokens.pop()]
    d = ""
    # A direction word is a prefix only if a name is left after it: "South Street" is SOUTH ST.
    if len(tokens) > 1 and tokens[0] in DIRS:
        d = DIRS[tokens.pop(0)]
    if not tokens:
        return None
    return StreetName(d, " ".join(tokens), typ)


def split_streets(text: str) -> list[str]:
    """ "16th, 17th, and 18th Streets" -> three streets; "A Drive & B Drive" -> two. Else [text]."""
    if "&" in text:
        return [p.strip() for p in text.split("&") if p.strip()]
    words = text.split()
    if words and words[-1].upper() in PLURAL_TYPES:
        singular = words[-1][:-1]
        head = " ".join(words[:-1])
        parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", head) if p.strip()]
        if len(parts) > 1:
            return [f"{p} {singular}" for p in parts]
    return [text]


# Centerline


@dataclass
class Segment:
    seg_id: int
    street: StreetName
    coords: list[Coord]
    addr_lo: int = 0  # lowest non-zero address on either side, 0 if none
    addr_hi: int = 0
    suf: str = ""     # suf_dir: E/W on Independence Mall and Logan Circle, ramp letters elsewhere
    cls: int = 0      # centerline class: 1 expressway, 2 major arterial, 3 minor arterial, 4 collector, 5 local; 0 unknown

    @property
    def ends(self) -> tuple[Coord, Coord]:
        return _node(self.coords[0]), _node(self.coords[-1])

    @property
    def length_m(self) -> float:
        return sum(_dist(a, b) for a, b in zip(self.coords, self.coords[1:]))


def _node(c: Iterable[float]) -> Coord:
    lon, lat = c
    return round(lon, 6), round(lat, 6)  # ~0.1 m


def _dist(a: Coord, b: Coord) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    dx = (b[0] - a[0]) * 111_320 * math.cos(lat)
    dy = (b[1] - a[1]) * 110_540
    return math.hypot(dx, dy)


def segment_from_feature(f: dict) -> Segment | None:
    p, g = f.get("properties") or {}, f.get("geometry")
    if not g or g.get("type") != "LineString" or len(g["coordinates"]) < 2:
        return None
    adds = [p.get(k) or 0 for k in ("l_f_add", "l_t_add", "r_f_add", "r_t_add")]
    adds = [a for a in adds if a > 0]
    street = StreetName((p.get("pre_dir") or "").strip(), (p.get("st_name") or "").strip(),
                        (p.get("st_type") or "").strip())
    return Segment(int(p["seg_id"]), street, [tuple(c) for c in g["coordinates"]],
                   min(adds, default=0), max(adds, default=0), (p.get("suf_dir") or "").strip(),
                   int(p.get("class") or 0))


def download_centerline(path: Path, client: httpx.Client | None = None) -> int:
    """Page through the layer and save it as GeoJSON in WGS84. Returns the feature count."""
    own = client is None
    client = client or httpx.Client(timeout=120)
    feats: list[dict] = []
    try:
        offset = 0
        while True:
            r = client.get(CENTERLINE_URL, params={
                "where": "1=1", "outFields": CENTERLINE_FIELDS, "outSR": 4326, "f": "geojson",
                "orderByFields": "objectid", "resultOffset": offset,
                "resultRecordCount": CENTERLINE_PAGE,
            })
            r.raise_for_status()
            page = r.json().get("features", [])
            feats.extend(page)
            if len(page) < CENTERLINE_PAGE:
                break
            offset += CENTERLINE_PAGE
    finally:
        if own:
            client.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    return len(feats)


def load_centerline(path: Path) -> list[Segment]:
    fc = json.loads(path.read_text())
    return [s for s in (segment_from_feature(f) for f in fc["features"]) if s]


# Matching


# Blocks that carry a different name in the centerline than in notices: the 1-199 blocks of
# 5th and 6th Street beside Independence Mall are INDEPENDENCE MALL E and W.
# (name, type) -> (st_name, st_type, suf_dir)
EQUIVALENTS = {
    ("5TH", "ST"): ("INDEPENDENCE", "MALL", "E"),
    ("6TH", "ST"): ("INDEPENDENCE", "MALL", "W"),
}


@dataclass
class Resolution:
    kind: str                      # from_to, block_range, named, one_end, area
    segments: list[Segment] = field(default_factory=list)
    reason: str = ""               # why it is unresolved; empty when resolved
    notes: list[str] = field(default_factory=list)  # name substitutions made to resolve it

    @property
    def ok(self) -> bool:
        return bool(self.segments)


class Network:
    """Centerline segments indexed by name.

    equivalents  extra centerline names a notice name also covers (see EQUIVALENTS).
    loose_types  in from-to chains only, a name whose type has no segments ("Larchwood Street")
                 may use the one other type that name has in the city (LARCHWOOD AVE). The chain
                 must still meet both cross streets, which checks the substitution.
    """

    def __init__(self, segments: list[Segment], equivalents: dict | None = None,
                 loose_types: bool = False):
        self.by_key: dict[str, list[Segment]] = defaultdict(list)
        for s in segments:
            self.by_key[s.street.key].append(s)
        self.equivalents = equivalents or {}
        self.loose_types = loose_types

    def find(self, street: StreetName) -> list[Segment]:
        """Segments carrying this name. A missing direction matches any; a missing type
        matches only segments that have none ("Avenue of the Republic")."""
        out = [s for s in self.by_key.get(street.key, [])
               if s.street.type == street.type and (not street.dir or s.street.dir == street.dir)]
        eq = self.equivalents.get((street.name, street.type))
        if eq:
            name, typ, suf = eq
            out += [s for s in self.by_key.get(name.replace(" ", ""), [])
                    if s.street.type == typ and s.suf == suf
                    and (not street.dir or s.street.dir == street.dir)]
        return out

    def lookup(self, text: str, loose: bool = False) -> tuple[list[Segment], str]:
        """(segments, reason when there are none, or a note when a substitution was made)."""
        street = normalize(text)
        if street is None:
            return [], f"no street name in {text!r}"
        segs = self.find(street)
        if segs:
            other = sorted({f"{s.street} {s.suf}".strip() for s in segs if s.street.key != street.key})
            return segs, f"included {', '.join(other)} as {street}" if other else ""
        if street.dir:
            # The direction word may be part of the name: "North Access Road" is NORTH ACCESS RD.
            whole = StreetName("", f"{DIR_WORDS[street.dir]} {street.name}", street.type)
            segs = self.find(whole)
            if segs:
                return segs, ""
        if loose and street.type:
            others = {s.street.type for s in self.by_key.get(street.key, [])}
            if len(others) == 1:
                alt = street._replace(type=others.pop())
                segs = self.find(alt)
                if segs:
                    return segs, f"read {street} as {alt}"
        return [], f"no centerline segment named {street}"

    def chain(self, street: str, frm: str, to: str,
              notes: list[str] | None = None) -> tuple[list[Segment], str]:
        """Segments of `street` between its intersections with `frm` and `to`.
        Name substitutions are appended to `notes`."""
        segs, note = self.lookup(street, self.loose_types)
        if not segs:
            return [], note
        found = [(note, normalize(street).key, segs)]

        def fail(reason: str) -> tuple[list[Segment], str]:
            tried = [n for n, _, _ in found if n.startswith("read")]
            return [], reason + (f" (tried: {'; '.join(tried)})" if tried else "")

        ends = []
        for cross in (frm, to):
            cross = _cross_part(cross, street)
            other, note = self.lookup(cross, self.loose_types)
            if not other:
                return fail(note)
            found.append((note, normalize(cross).key, other))
            other_nodes = {n for s in other for n in s.ends}
            hits = {n for s in segs for n in s.ends} & other_nodes
            if not hits:
                return fail(f"{normalize(street)} does not meet {normalize(cross)}")
            ends.append(hits)
        start, goal = ends
        if start & goal:
            return fail(f"{normalize(frm)} and {normalize(to)} meet {normalize(street)} at the same point")
        path = _shortest_path(segs, start, goal)
        if path is None:
            return fail(f"no connected path along {normalize(street)} between the two intersections")
        if notes is not None:
            # An equivalent name is noted only if one of its segments is in the chain or meets
            # it, not merely because it was a candidate. A type substitution is always noted.
            path_ids = {s.seg_id for s in path}
            corners = {n for s in path for n in s.ends} & (start | goal)
            for i, (note, key, cand) in enumerate(found):
                subs = [s for s in cand if s.street.key != key]
                if i == 0:
                    used = any(s.seg_id in path_ids for s in subs)
                else:
                    used = any(set(s.ends) & corners for s in subs)
                if note and (note.startswith("read") or used) and note not in notes:
                    notes.append(note)
        return path, ""

    def blocks(self, street: str, lo: int, hi: int,
               frm: str = "", to: str = "") -> tuple[list[Segment], str]:
        """Segments of `street` whose addresses overlap blocks lo..hi (hi block inclusive)."""
        segs, why = self.lookup(street)
        if not segs:
            return [], why
        top = hi + 99
        hits = [s for s in segs if s.addr_lo and s.addr_lo <= top and s.addr_hi >= lo]
        if not hits:
            return [], f"no {normalize(street)} segment has addresses {lo}-{top}"
        by_dir: dict[str, list[Segment]] = defaultdict(list)
        for s in hits:
            by_dir[s.street.dir].append(s)
        if len(by_dir) > 1:
            # Same block numbers north and south of Market: keep the side the cross streets touch.
            cross_nodes = set()
            for cross in (frm, to):
                other = self.find(normalize(cross)) if cross and normalize(cross) else []
                cross_nodes |= {n for s in other for n in s.ends}
            touching = {d: g for d, g in by_dir.items()
                        if any(n in cross_nodes for s in g for n in s.ends)}
            if len(touching) != 1:
                sides = "/".join(sorted(d or "-" for d in by_dir))
                return [], f"blocks {lo}-{hi} of {normalize(street)} exist on {sides} sides; cross streets do not pick one"
            hits = next(iter(touching.values()))
        return hits, ""


def _cross_part(cross: str, street: str) -> str:
    """ "6th/Market" on 6th Street means Market."""
    if "/" not in cross:
        return cross
    me = normalize(street)
    parts = [p for p in cross.split("/") if p.strip()]
    rest = [p for p in parts if not (me and normalize(p) and normalize(p).name == me.name)]
    return rest[0] if len(rest) == 1 else cross


def _shortest_path(segs: list[Segment], start: set[Coord], goal: set[Coord]) -> list[Segment] | None:
    adj: dict[Coord, list[tuple[Coord, Segment]]] = defaultdict(list)
    for s in segs:
        a, b = s.ends
        adj[a].append((b, s))
        adj[b].append((a, s))
    dist = {n: 0.0 for n in start}
    prev: dict[Coord, tuple[Coord, Segment]] = {}
    heap = [(0.0, n) for n in start]
    heapq.heapify(heap)
    while heap:
        d, n = heapq.heappop(heap)
        if d > dist.get(n, math.inf):
            continue
        if n in goal:
            path = []
            while n in prev:
                n, s = prev[n]
                path.append(s)
            return path[::-1]
        for m, s in adj[n]:
            nd = d + s.length_m
            if nd < dist.get(m, math.inf):
                dist[m] = nd
                prev[m] = (n, s)
                heapq.heappush(heap, (nd, m))
    return None


def resolve(row: dict, net: Network) -> Resolution:
    """Match one event_closures row (street, from_street, to_street) to segments."""
    street = (row.get("street") or "").strip()
    frm = (row.get("from_street") or "").strip()
    to = (row.get("to_street") or "").strip()

    if AREA_RE.search(street) or " to " in frm.lower() or " to " in to.lower():
        return Resolution("area", reason="area or free-text list, not a single street")

    m = BLOCK_RE.match(street)
    if m:
        lo = int(m.group(1))
        hi = int(m.group(2) or lo)
        segs, why = net.blocks(m.group(3), lo, hi, frm, to)
        return Resolution("block_range", segs, why)

    if bool(frm) != bool(to):
        return Resolution("one_end", reason=f"only one end given ({frm or to!r})")

    names = split_streets(street)
    out: list[Segment] = []
    notes: list[str] = []
    if not frm:
        for name in names:
            segs, why = net.lookup(name)
            if not segs:
                return Resolution("named", reason=why)
            out += segs
            if why:
                notes.append(why)
        return Resolution("named", _dedupe(out), notes=notes)
    for name in names:
        segs, why = net.chain(name, frm, to, notes)
        if not segs:
            return Resolution("from_to", reason=why)
        out += segs
    return Resolution("from_to", _dedupe(out), notes=notes)


def resolve_route(legs: list[dict], net: Network, note: str = "") -> Resolution:
    """A closure whose notice names no usable end ("Erie Avenue to Navy Yard"), resolved from
    curated legs instead: each leg is a street from X to Y, chained in order. Every leg must
    resolve or the whole route is unresolved."""
    out: list[Segment] = []
    notes = [note] if note else []
    for i, leg in enumerate(legs, 1):
        segs, why = net.chain(leg["street"], leg["from_street"], leg["to_street"], notes)
        if not segs:
            return Resolution("route", reason=f"route leg {i} ({leg['street']}): {why}")
        out += segs
    return Resolution("route", _dedupe(out), notes=notes)


def _dedupe(segs: list[Segment]) -> list[Segment]:
    seen, out = set(), []
    for s in segs:
        if s.seg_id not in seen:
            seen.add(s.seg_id)
            out.append(s)
    return out


def to_geojson(features: list[tuple[list[Segment], dict]]) -> dict:
    """FeatureCollection of MultiLineStrings, one per (segments, properties)."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": props,
         "geometry": {"type": "MultiLineString", "coordinates": [list(map(list, s.coords)) for s in segs]}}
        for segs, props in features
    ]}
