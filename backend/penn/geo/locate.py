"""Turn what the user said or clicked into H3 cells. The model never invents coordinates.

Order: map click -> named area -> address/intersection (AIS geocoder) -> ask for a pin.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..config import settings
from .h3util import point_cell, polygon_cells, ring

AREAS_DIR = Path(__file__).resolve().parents[3] / "data" / "areas"
# Verify the AIS endpoint, key requirement and terms during recon.
AIS_URL = "https://api.phila.gov/ais/v1/search/{q}"


@dataclass
class Resolved:
    cells: list[int] = field(default_factory=list)
    label: str = ""
    method: str = ""          # click | area | geocode | none
    needs_pin: bool = False


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


def from_click(lon: float, lat: float, k: int = 1) -> Resolved:
    c = point_cell(lon, lat)
    return Resolved(cells=ring(c, k), label=f"{lat:.5f}, {lon:.5f}", method="click")


def from_text(text: str, client: httpx.Client | None = None) -> Resolved:
    t = text.strip().lower()
    for name, cells in areas().items():
        if name in t:
            return Resolved(cells=cells, label=name.title(), method="area")
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
