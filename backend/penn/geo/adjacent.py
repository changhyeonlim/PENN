"""The records beside a place, which the map lights besides an answer's citations: ask about Rittenhouse Square and
the block parties on Walnut Street, along its north side, light up whether or not the answer names them.

Beside means within REACH_M of the place: of a landmark's outline (its bounds), or of a corner's or an address's
point. A group of records (one event's permits, one announced closure's streets) is lit whole when any of it is
beside the place. A neighborhood is itself the place: all that its search found, but the routine lane work.
"""
from __future__ import annotations

import re
from math import cos, radians

from .. import db
from . import landmarks

REACH_M = 45            # across a street from the place's edge
M_PER_DEG = 111_320

Box = tuple[float, float, float, float]   # west, south, east, north


def _box(place) -> Box | None:
    """The place's outline, reached out by REACH_M; None for a neighborhood (all of it is the place)."""
    loc = place.loc
    if loc.method == "landmark":
        found = landmarks.find(place.label)
        mark = next((l for l in found if l.name == place.label), found[0] if found else None)
        (w, s), (e, n) = mark.bounds if mark else (loc.point, loc.point)
    elif loc.point:
        (w, s), (e, n) = loc.point, loc.point
    else:
        return None
    dy = REACH_M / M_PER_DEG
    dx = REACH_M / (M_PER_DEG * cos(radians((s + n) / 2)))
    return w - dx, s - dy, e + dx, n + dy


def _parts(wkt: str) -> list[list[tuple[float, float]]]:
    """The coordinates of a WKT point, line or multi-geometry, part by part."""
    return [[(float(x), float(y)) for x, y in re.findall(r"(-?\d+\.?\d*)\s+(-?\d+\.?\d*)", part)]
            for part in re.split(r"\)\s*,\s*\(", wkt)]


def _crosses(a: tuple[float, float], b: tuple[float, float], box: Box) -> bool:
    """Whether the segment a-b enters the box (Liang-Barsky)."""
    w, s, e, n = box
    t0, t1 = 0.0, 1.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    for p, q in ((-dx, a[0] - w), (dx, e - a[0]), (-dy, a[1] - s), (dy, n - a[1])):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def _beside(wkt: str, box: Box) -> bool:
    for pts in _parts(wkt or ""):
        if len(pts) == 1 and _crosses(pts[0], pts[0], box):
            return True
        if any(_crosses(a, b, box) for a, b in zip(pts, pts[1:])):
            return True
    return False


def beside(groups: list[tuple[str, list[str]]], place, skip: set[str] = frozenset()) -> list[str]:
    """The ids of the groups beside the place, in the search's order, leaving out `skip` (the cited)."""
    box = _box(place) if place else None
    if place and box is None:
        keep = [ids for section, ids in groups if section != "lane_work"]
    else:
        ids = list(dict.fromkeys(i for _, g in groups for i in g))
        if not ids or box is None:
            return []
        rows = db.query("SELECT event_id, geometry_wkt FROM events FINAL WHERE event_id IN %(ids)s", {"ids": ids})
        near = {r["event_id"] for r in rows if _beside(r["geometry_wkt"], box)}
        keep = [g for _, g in groups if any(i in near for i in g)]
    return [i for i in dict.fromkeys(i for g in keep for i in g) if i not in skip]
