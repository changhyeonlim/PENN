import json

import pytest

from penn.ingestion.recurring_events import CurationError, load_file, recurrence

ROUTE = {"type": "LineString", "coordinates": [[-75.1636, 39.9526], [-75.1636, 39.9450]]}


def edition(year, **over):
    p = {"series": "test-run", "name": f"Test Run {year}", "year": year, "date": f"{year}-05-03",
         "closure_start": "06:00", "closure_end": "12:00", "source_url": "https://example.org/route"}
    p.update(over)
    return {"type": "Feature", "geometry": ROUTE, "properties": p}


def test_loads_curated_editions(tmp_path):
    f = tmp_path / "test-run.geojson"
    f.write_text(json.dumps({"type": "FeatureCollection", "features": [edition(2024), edition(2025)]}))
    evs = load_file(f)
    assert {e.source_record_id for e in evs} == {"test-run-2024", "test-run-2025"}
    assert all(e.source_authority == "curated" for e in evs)
    rec = recurrence(evs, set(evs[0].h3_cells[:1]))
    assert rec["test-run"] == {"editions": 2, "touched": 2, "years": [2024, 2025]}


def test_edition_without_source_is_refused(tmp_path):
    f = tmp_path / "bad.geojson"
    f.write_text(json.dumps({"type": "FeatureCollection", "features": [edition(2024, source_url="")]}))
    with pytest.raises(CurationError):
        load_file(f)


AREA = {"type": "Polygon", "coordinates": [[[-75.160, 39.9420], [-75.145, 39.9420], [-75.145, 39.9430],
                                             [-75.160, 39.9430], [-75.160, 39.9420]]]}
VENUE = {"type": "Point", "coordinates": [-75.1675, 39.9008]}


def test_area_venue_and_multiday(tmp_path):
    feats = [
        {"type": "Feature", "geometry": AREA, "properties": {"series": "odunde", "name": "Odunde 2025", "year": 2025,
         "date": "2025-06-08", "source_url": "https://example.org/odunde"}},
        {"type": "Feature", "geometry": VENUE, "properties": {"series": "fifa", "name": "World Cup matches", "year": 2026,
         "date": "2026-06-14", "end_date": "2026-07-04", "source_url": "https://example.org/fifa"}},
    ]
    f = tmp_path / "mixed.geojson"
    f.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    odunde, fifa = load_file(f)
    assert odunde.occupancy == "area" and len(odunde.h3_cells) > 1
    assert fifa.occupancy == "venue" and len(fifa.h3_cells) == 1
    assert (fifa.end_date - fifa.start_date).days == 20


def test_end_before_start_refused(tmp_path):
    f = tmp_path / "bad.geojson"
    f.write_text(json.dumps({"type": "FeatureCollection", "features": [
        edition(2024, end_date="2024-05-01")]}))
    with pytest.raises(CurationError):
        load_file(f)
