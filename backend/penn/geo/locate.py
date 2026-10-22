"""Turn what the user said or clicked into H3 cells. The model never invents coordinates.

Order: map click -> typed intersection (street centerline, offline) -> named area
-> address (AIS geocoder) -> ask for a pin.
The intersection goes before areas so "23rd and Fairmount" is the corner, not the
Fairmount neighborhood.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import h3
import httpx

from ..config import settings
from . import intersections, landmarks
from .h3util import line_cells, point_cell, polygon_cells, ring

AREAS_DIR = Path(__file__).resolve().parents[3] / "data" / "areas"
# Verify the AIS endpoint, key requirement and terms during recon.
AIS_URL = "https://api.phila.gov/ais/v1/search/{q}"


@dataclass
class Resolved:
    cells: list[int] = field(default_factory=list)
    label: str = ""
    method: str = ""          # click | intersection | landmark | area | geocode | outside | none
    needs_pin: bool = False
    seg_ids: list[int] = field(default_factory=list)   # intersection legs (centerline seg_id)
    point: tuple[float, float] | None = None            # (lon, lat) of a corner, address or click, for the map's marker
    inside: frozenset[int] = frozenset()                # a neighborhood's outline in fine cells (area_inside)


NAME_KEYS = ("LISTNAME", "MAPNAME", "name", "NAME")  # OpenDataPhilly neighborhoods use LISTNAME


def _feature_name(props: dict) -> str:
    for k in NAME_KEYS:
        v = props.get(k)
        if v:
            return str(v).replace("_", " ").strip().lower()
    return ""


def _load_areas() -> dict[str, list[int]]:
    """data/areas/*.geojson polygons, plus data/areas/aliases.json for areas made of several
    neighborhoods (for example "center city"). Returned longest name first, so "logan square"
    is matched before "logan"."""
    areas: dict[str, list[int]] = {}
    if not AREAS_DIR.exists():
        return areas
    for path in AREAS_DIR.glob("*.geojson"):
        for f in json.loads(path.read_text()).get("features", []):
            g = f.get("geometry") or {}
            name = _feature_name(f.get("properties") or {})
            if not name:
                continue
            polys = [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", [])
            cells: list[int] = []
            for rings in polys:
                cells.extend(polygon_cells(rings))
            areas[name] = cells
    alias_file = AREAS_DIR / "aliases.json"
    if alias_file.exists():
        for alias, members in json.loads(alias_file.read_text()).items():
            if alias.startswith("_"):
                continue
            cells = sorted({c for m in members for c in areas.get(m.lower(), [])})
            missing = [m for m in members if m.lower() not in areas]
            if missing:
                raise ValueError(f"aliases.json: {alias!r} names unknown areas {missing}")
            areas[alias.lower()] = cells
    return dict(sorted(areas.items(), key=lambda kv: -len(kv[0])))


_AREAS: dict[str, list[int]] | None = None


def areas() -> dict[str, list[int]]:
    global _AREAS
    if _AREAS is None:
        _AREAS = _load_areas()
    return _AREAS


# An r9 cell is ~350 m across, so a neighborhood's cells reach a block or two past its outline: asked about
# Fairmount, the search found milling on W Harper St in Francisville, 190 m outside, and the brief led with it.
# The outline in r11 cells (~50 m) says which streets are really in the area; one ring more keeps the streets
# that form its edge (Poplar St is Fairmount's).
INSIDE_RES = 11


@lru_cache(maxsize=64)
def area_inside(name: str) -> frozenset[int]:
    """The named area's outline as r11 cells plus a ~50 m margin; empty for a name with no outline."""
    alias_file = AREAS_DIR / "aliases.json"
    aliases = {k.lower(): v for k, v in json.loads(alias_file.read_text()).items()} if alias_file.exists() else {}
    wanted = {m.lower() for m in aliases.get(name, [name])}
    cells: set[str] = set()
    for path in AREAS_DIR.glob("*.geojson"):
        for f in json.loads(path.read_text()).get("features", []):
            if _feature_name(f.get("properties") or {}) not in wanted:
                continue
            g = f.get("geometry") or {}
            for rings in [g["coordinates"]] if g.get("type") == "Polygon" else g.get("coordinates", []):
                cells.update(h3.int_to_str(c) for c in polygon_cells(rings, INSIDE_RES))
    return frozenset(h3.str_to_int(n) for c in cells for n in h3.grid_disk(c, 1))


def from_click(lon: float, lat: float, k: int = 1) -> Resolved:
    c = point_cell(lon, lat)
    cells = ring(c, k)
    city = city_cells()
    if city and not city.intersection(cells):          # the whole ring is past the city line
        return outside(f"{lat:.5f}, {lon:.5f}")
    return Resolved(cells=cells, label=f"{lat:.5f}, {lon:.5f}", method="click", point=(lon, lat))


def from_intersection(hit: intersections.Intersection) -> Resolved:
    """Cells along the legs (one block each way) plus the corner itself."""
    lon, lat = hit.node
    cells = {point_cell(lon, lat)}
    for s in hit.legs:
        cells.update(line_cells(s.coords))
    return Resolved(cells=sorted(cells), label=hit.label, method="intersection", seg_ids=hit.seg_ids, point=(lon, lat))


def from_landmark(lm: landmarks.Landmark) -> Resolved:
    """A named place: its cells (a disk for a building or park, the segments' cells for a corridor)."""
    return Resolved(cells=list(lm.cells), label=lm.name, method="landmark", point=lm.point)


# Places people ask about that are outside the city. PENN's records stop at the city line, so
# these are refused before any search ("no record" would wrongly suggest PENN looked).
OUTSIDE = re.compile(r"\b(camden|new jersey|n\.?j\.?|cherry hill|pennsauken|upper darby|lower merion|cheltenham|"
                     r"king of prussia|bala cynwyd|delaware county|delco|montgomery county|bucks county|"
                     r"chester county|wilmington|trenton|princeton)\b", re.I)


def city_cells() -> set[int]:
    """Every H3 cell inside a Philadelphia neighborhood polygon: the city limits, at r9."""
    global _CITY
    if _CITY is None:
        _CITY = {c for cells in areas().values() for c in cells}
    return _CITY


_CITY: set[int] | None = None


def outside(label: str) -> Resolved:
    return Resolved(method="outside", label=label)


STREET_SUFFIX = re.compile(r"\s+(street|st|avenue|ave|av|road|rd|lane|ln|place|pl|boulevard|blvd|drive|dr|way|"
                           r"terrace|ter|court|ct|pike)\b")


def _names_street(area: str, after: str) -> bool:
    """Whether the area name is the start of a real street's name ("Ludlow Street", "Rittenhouse St",
    "Fairmount Ave"): then the user means the street, not the neighborhood, which may be across town."""
    m = STREET_SUFFIX.match(after)
    return bool(m and intersections.network().lookup(f"{area} {m.group(1)}")[0])


def from_text(text: str, client: httpx.Client | None = None) -> Resolved:
    if m := OUTSIDE.search(text):
        return outside(m.group(0).title())
    hit = intersections.find(text)
    if hit:
        return from_intersection(hit)
    t = text.strip().lower()
    area = None
    for name, cells in areas().items():
        m = re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", t)   # whole words: "logan" not in "slogan"
        if m and not _names_street(name, t[m.end():]):
            area = (name, cells)
            break
    # A landmark (p3-10) named by a longer phrase than any neighborhood wins: "Rittenhouse Square" is the
    # park, "Rittenhouse" the neighborhood; "the Art Museum" and "Kelly Drive" name no neighborhood at all.
    lm = landmarks.best(text)
    if lm and (area is None or lm[1] > len(area[0])):
        return from_landmark(lm[0])
    if area:
        return Resolved(cells=area[1], label=area[0].title(), method="area", inside=area_inside(area[0]))
    q = text.replace(" and ", " & ")
    try:
        c = client or httpx.Client(timeout=10)
        params = {"gatekeeperKey": settings.ais_gatekeeper_key} if settings.ais_gatekeeper_key else {}
        r = c.get(AIS_URL.format(q=q), params=params)
        if r.status_code == 200:
            feats = r.json().get("features", [])
            if feats:
                lon, lat = feats[0]["geometry"]["coordinates"]
                res = from_click(lon, lat)
                res.method, res.label = "geocode", feats[0].get("properties", {}).get("street_address", text)
                return res
    except httpx.HTTPError:
        pass
    return Resolved(method="none", needs_pin=True, label=text)
