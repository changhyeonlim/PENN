"""H3 helpers. ClickHouse stores cells as UInt64; these functions return ints."""
from __future__ import annotations

import math

import h3

from ..config import H3_RES

Coord = tuple[float, float]  # (lon, lat), GeoJSON order


def mercator_to_wgs84(x: float, y: float) -> Coord:
    """EPSG:3857 metres to (lon, lat). Only needed if a source returns Web Mercator."""
    lon = x / 6378137.0 * 180.0 / math.pi
    lat = math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)
    return lon, lat


def point_cell(lon: float, lat: float, res: int = H3_RES) -> int:
    return h3.str_to_int(h3.latlng_to_cell(lat, lon, res))


def line_cells(coords: list[Coord], res: int = H3_RES) -> list[int]:
    """Every cell a polyline passes through, in order, without duplicates."""
    out: list[str] = []
    prev = None
    for lon, lat in coords:
        c = h3.latlng_to_cell(lat, lon, res)
        if prev is None:
            path = [c]
        else:
            try:
                path = h3.grid_path_cells(prev, c)
            except Exception:  # pentagon edge cases; fall back to the vertex cell
                path = [c]
        for cell in path:
            if not out or out[-1] != cell:
                out.append(cell)
        prev = c
    seen, uniq = set(), []
    for cell in out:
        if cell not in seen:
            seen.add(cell)
            uniq.append(h3.str_to_int(cell))
    return uniq


def polygon_cells(rings: list[list[Coord]], res: int = H3_RES) -> list[int]:
    """Cells whose centres fall inside a GeoJSON polygon (outer ring first)."""
    outer = [(lat, lon) for lon, lat in rings[0]]
    holes = [[(lat, lon) for lon, lat in r] for r in rings[1:]]
    shape = h3.LatLngPoly(outer, *holes)
    return [h3.str_to_int(c) for c in h3.polygon_to_cells(shape, res)]


def ring(cell: int, k: int = 1) -> list[int]:
    return [h3.str_to_int(c) for c in h3.grid_disk(h3.int_to_str(cell), k)]


def centroid(coords: list[Coord]) -> Coord:
    lon = sum(c[0] for c in coords) / len(coords)
    lat = sum(c[1] for c in coords) / len(coords)
    return lon, lat


def multiline_wkt(lines: list[list[Coord]]) -> str:
    parts = ", ".join("(" + ", ".join(f"{x:.6f} {y:.6f}" for x, y in line) + ")" for line in lines)
    return f"MULTILINESTRING ({parts})"
