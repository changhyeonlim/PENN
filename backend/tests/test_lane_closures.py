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


def test_placeholder_expiry_flagged():
    evs = group_by_permit([feat(1, "2024-00565", SEG_A, eff=1705276800000, exp=4198176000000)])
    assert evs[0].open_ended


def test_snapshot_counts_segments():
    rows = snapshot_rows([feat(1, "P1", SEG_A), feat(2, "P1", SEG_B)], date(2026, 10, 5))
    assert len(rows) == 1 and rows[0]["segment_count"] == 2
