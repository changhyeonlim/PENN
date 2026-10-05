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
