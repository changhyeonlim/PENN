"""Typed intersections ("near 12th and Walnut on Saturday") resolved offline on the street centerline.

An intersection is the node where a segment of street A and a segment of street B share an
endpoint. Its legs are the segments that touch that node, one block in each direction, and
their seg_ids are the same IDs Lane Closures permits carry.

People type names without a suffix ("Walnut", "23rd"), so a name with no type matches every
type the city uses for it; the intersection test then picks the pair that actually meets.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .h3util import Coord
from .streets import Network, Segment, load_centerline, normalize

CENTERLINE = Path(__file__).resolve().parents[3] / "data" / "centerline" / "street_centerline.geojson"
CONNECTOR = re.compile(r"\s+(?:and|&|at)\s+", re.IGNORECASE)
# Words that are never part of a street name in a question.
FILLER = {"near", "at", "on", "of", "the", "around", "by", "this", "that", "corner", "intersection",
          "in", "is", "are", "there", "any", "what", "whats", "anything", "for", "from", "to", "and",
          "today", "tomorrow", "tonight", "weekend", "week", "happening", "work", "street-work"}
MAX_WORDS = 3
# Nodes of one intersection on a divided road are a few dozen metres apart; farther apart
# means the two names cross in more than one place.
CLUSTER_M = 120


@dataclass
class Intersection:
    label: str                       # "Walnut St & S 12th St"
    node: Coord                      # (lon, lat), mean of the meeting nodes
    legs: list[Segment] = field(default_factory=list)

    @property
    def seg_ids(self) -> list[int]:
        return sorted({s.seg_id for s in self.legs})


@lru_cache(maxsize=1)
def network() -> Network:
    return Network(load_centerline(CENTERLINE))


def _named(net: Network, text: str) -> list[Segment]:
    """Segments for a typed name. No type given = any type that name has."""
    street = normalize(text)
    if street is None:
        return []
    return [s for s in net.by_key.get(street.key, [])
            if (not street.type or s.street.type == street.type)
            and (not street.dir or s.street.dir == street.dir)]


def _dist(a: Coord, b: Coord) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((b[0] - a[0]) * 111_320 * math.cos(lat), (b[1] - a[1]) * 110_540)


def meet(a: list[Segment], b: list[Segment]) -> Intersection | None:
    """Where two streets meet, or None if they never do or meet in more than one place."""
    nodes = {n for s in a for n in s.ends} & {n for s in b for n in s.ends}
    if not nodes:
        return None
    if max((_dist(p, q) for p in nodes for q in nodes), default=0) > CLUSTER_M:
        return None
    legs = _whole_blocks([s for s in a + b if set(s.ends) & nodes], a + b)
    lon = sum(n[0] for n in nodes) / len(nodes)
    lat = sum(n[1] for n in nodes) / len(nodes)
    names = []
    for s in legs:   # one name per street, in A-then-B order
        n = _pretty(str(s.street))
        if n not in names:
            names.append(n)
    return Intersection(" & ".join(names[:2]), (lon, lat), legs)


def _blocks(s: Segment) -> set[int]:
    return {x // 100 for x in (s.addr_lo, s.addr_hi) if x}


def _whole_blocks(touching: list[Segment], same_streets: list[Segment]) -> list[Segment]:
    """The centerline splits blocks at alleys, so the segment touching the corner can be a third of
    the block. Extend each leg to every segment of the same street in the same hundred block
    ("1300 block of Locust"). Segments without addresses (inner drives) stay as they are."""
    out = {s.seg_id: s for s in touching}
    for leg in touching:
        blocks = _blocks(leg)
        if not blocks:
            continue
        for s in same_streets:
            if str(s.street) == str(leg.street) and _blocks(s) & blocks:
                out.setdefault(s.seg_id, s)
    return list(out.values())


def _pretty(name: str) -> str:
    return " ".join(w if len(w) == 1 else w.capitalize() for w in name.split())   # "S 12TH ST" -> "S 12th St"


def _words(text: str) -> list[str]:
    return re.sub(r"[?!.,;:\"()]", " ", text).split()


def candidates(text: str) -> list[tuple[str, str]]:
    """Every (left, right) name pair around a connector word, longest first."""
    out = []
    for m in CONNECTOR.finditer(text):
        left, right = _words(text[:m.start()]), _words(text[m.end():])
        while right and right[0].lower() in ("the", "of"):    # "22nd and the Parkway"
            right = right[1:]
        for i in range(1, MAX_WORDS + 1):
            lw = left[-i:]
            if len(lw) < i or any(w.lower() in FILLER for w in lw):
                break
            for j in range(1, MAX_WORDS + 1):
                rw = right[:j]
                if len(rw) < j or any(w.lower() in FILLER for w in rw):
                    break
                out.append((" ".join(lw), " ".join(rw)))
    return sorted(out, key=lambda p: -(len(p[0].split()) + len(p[1].split())))


def find(text: str, net: Network | None = None) -> Intersection | None:
    """The first intersection named in free text, preferring the most specific wording."""
    if not CONNECTOR.search(text):
        return None
    net = net or network()
    for left, right in candidates(text):
        hit = meet(_named(net, left), _named(net, right))
        if hit:
            return hit
    return None
