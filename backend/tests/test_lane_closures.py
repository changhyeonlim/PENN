from penn.ingestion.lane_closures import group_by_permit, snapshot_rows
from datetime import date

def feat(oid, permit, coords, occ="Full Closure", ptype="Utility Work Excavation",
         eff=1747699200000, exp=1796342400000, addr="1700 block of JUDSON ST"):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"objectid": oid, "permitnumber": permit, "permittype": ptype,
                           "occupancytype": occ, "purpose": "STORM WATER INSTALLATION",
                           "status": "Current", "effectivedate": eff, "expirationdate": exp,
                           "address": addr}}

SEG_A = [[-75.1700, 39.9500], [-75.1690, 39.9502]]
SEG_B = [[-75.1690, 39.9502], [-75.1680, 39.9504]]


def test_segments_of_one_permit_become_one_event():
    evs = group_by_permit([feat(1, "2025-07887", SEG_A), feat(2, "2025-07887", SEG_B),
                           feat(3, "2025-08198", SEG_A, addr="2400 block of S 5TH ST")])
    assert len(evs) == 2
    e = next(x for x in evs if x.source_record_id == "2025-07887")
    assert e.geometry_wkt.startswith("MULTILINESTRING")
    assert e.status == "permitted" and e.claim_certainty == "scheduled"
    assert e.event_type == "utility_work"
    assert len(e.h3_cells) >= 1


def test_parking_permits_are_typed():
    evs = group_by_permit([feat(1, "2025-24394", SEG_A, ptype="Temporary No Parking", occ="Partial Closure")])
    assert evs[0].event_type == "parking_restriction"


def test_permit_types_are_mapped():
    cases = {"Block Party": "block_party", "Temporary Passenger Loading Zone": "parking_restriction",
             "PGW EUN Excavation": "utility_work", "Equipment Placement": "construction",
             "Municipal Contract Work": "roadwork", "Milling/Resurfacing/Traffic Calming": "roadwork",
             "Some New Type": "other"}
    for i, (ptype, expected) in enumerate(cases.items()):
        evs = group_by_permit([feat(i, f"P{i}", SEG_A, ptype=ptype)])
        assert evs[0].event_type == expected, ptype


def test_planning_only_is_not_scheduled():
    evs = group_by_permit([feat(1, "G2023-00166", SEG_A, ptype="Milling/Resurfacing - Planning Only", exp=None)])
    e = evs[0]
    assert e.event_type == "roadwork"
    assert e.claim_certainty == "planned" and e.status == "planning_only"
    assert e.open_ended


def test_placeholder_expiry_flagged():
    evs = group_by_permit([feat(1, "2024-00565", SEG_A, eff=1705276800000, exp=4198176000000)])
    assert evs[0].open_ended


def test_snapshot_counts_segments():
    rows = snapshot_rows([feat(1, "P1", SEG_A), feat(2, "P1", SEG_B)], date(2026, 10, 5))
    assert len(rows) == 1 and rows[0]["segment_count"] == 2


# --- fetch_features against a fake ArcGIS server -------------------------------------------

import httpx
import pytest
from penn.ingestion import lane_closures as lc


def fake_client(count: int, pages: list, error_on_page: int | None = None) -> httpx.Client:
    """pages[i] is the number of features on page i; exceededTransferLimit is set on all but the last."""
    def handler(req: httpx.Request) -> httpx.Response:
        q = req.url.params
        if q.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": count})
        i = int(q["resultOffset"]) // lc.LANE_CLOSURES_PAGE
        if i == error_on_page:
            return httpx.Response(200, json={"error": {"code": 500, "message": "boom"}})
        n = pages[i] if i < len(pages) else 0
        body = {"type": "FeatureCollection", "features": [{"properties": {"objectid": j}} for j in range(n)]}
        if i < len(pages) - 1:
            body["properties"] = {"exceededTransferLimit": True}
        return httpx.Response(200, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_reads_every_page():
    page = lc.LANE_CLOSURES_PAGE
    assert len(list(lc.fetch_features(fake_client(page + 5, [page, 5])))) == page + 5


def test_fetch_raises_on_error_body_with_http_200():
    page = lc.LANE_CLOSURES_PAGE
    with pytest.raises(lc.FetchError, match="ArcGIS error"):
        list(lc.fetch_features(fake_client(page + 5, [page, 5], error_on_page=1)))


def test_fetch_raises_when_total_is_short():
    page = lc.LANE_CLOSURES_PAGE
    with pytest.raises(lc.FetchError, match="layer reports"):
        list(lc.fetch_features(fake_client(page + 5, [page])))
