from penn.agent import impact

W0 = "2026-10-19T00:00:00-04:00"


def permit(i, occ="Partial Closure", et="utility_work", start="2026-10-20", end="2026-10-22", **kw):
    return {"event_id": f"lane_closures:{i}", "source": "lane_closures", "event_type": et, "title": f"Permit {i}",
            "occupancy": occ, "start_date": start, "end_date": end, "claim_certainty": "scheduled",
            "open_ended": False, "seg_ids": [], **kw}


def test_tiers():
    assert impact.tier(permit(1, "Footway", "construction"), 2) == "footway"
    assert impact.tier(permit(2, et="parking_restriction"), 2) == "parking"
    assert impact.tier(permit(3), 5) == "side_lane"                                   # lane work, local street
    assert impact.tier(permit(4), 2) == "getting_around"                              # short, arterial
    assert impact.tier(permit(5, end="2027-03-01"), 2) == "long_lane"                 # months of lane work
    assert impact.tier(permit(6, "Full Closure"), 3) == "getting_around"
    assert impact.tier(permit(7, "Full Closure"), 4) == "getting_around"              # collector still counts
    assert impact.tier(permit(8, "Full Closure", "block_party"), 5) == "block_party"  # side-street block party
    assert impact.tier(permit(9, "Full Closure"), None) == "getting_around"           # no legs to check: assume it matters
    assert impact.tier(permit(10, "Full Closure", open_ended=True), 2) == "planned"
    assert impact.tier({"source": "visitphilly", "event_type": "event"}, None) == "event"
    assert impact.tier({"source": "ticketmaster", "event_type": "event"}, None) == "event"
    assert impact.tier({"source": "event_closures", "event_type": "event"}, None) == "getting_around"


def test_same_day_permits_group_into_one_story(monkeypatch):
    monkeypatch.setattr(impact, "_seg_class", lambda seg_ids: 2)
    rows = [permit(i, "Full Closure", "block_party", "2026-10-25", "2026-10-25") for i in range(8)]
    rows.append(permit(99, "Full Closure", "utility_work", "2026-10-21", "2026-11-30"))
    b = impact.brief(rows, W0)
    new = b["getting_around"]["new"]
    assert len(new) == 2
    group = new[0]                                     # the biggest disruption comes first
    assert len(group["event_ids"]) == 8 and group["cite"] == "[id:lane_closures:0]" and group["main_street"]


def test_notice_closures_group_by_notice_and_day(monkeypatch):
    monkeypatch.setattr(impact, "_seg_class", lambda seg_ids: 2)
    ec = lambda i, where: {"event_id": f"event_closures:{i}", "source": "event_closures", "event_type": "event",
                           "title": f"Philly Bike Ride: {where}", "start_ts": "2026-10-24T07:30:00-04:00",
                           "end_ts": "2026-10-24T14:00:00-04:00", "seg_ids": []}
    b = impact.brief([ec(1, "Eakins Oval"), ec(2, "22nd St, Christian St to the Parkway")], W0)
    (g,) = b["getting_around"]["new"]
    assert g["group"] == "Philly Bike Ride" and [c["where"] for c in g["closures"]] == [
        "Eakins Oval", "22nd St, Christian St to the Parkway"]


def test_ongoing_and_minor_counts(monkeypatch):
    monkeypatch.setattr(impact, "_seg_class", lambda seg_ids: 5)
    rows = [permit(1, "Footway", "construction"), permit(2, et="parking_restriction"), permit(3),
            {**permit(4, "Full Closure", start="2026-09-01", end="2026-12-31"), "seg_ids": []}]
    monkeypatch.setattr(impact, "_seg_class", lambda seg_ids: None if not seg_ids else 5)
    b = impact.brief(rows, W0)
    assert [i["event_id"] for i in b["getting_around"]["ongoing"]] == ["lane_closures:4"]   # started before the window
    assert b["minor"]["count"] == 3
    assert set(b["minor"]["by_kind"]) == {"sidewalk work", "parking and loading zones", "lane work on side streets"}
    assert "seg_ids" not in b["getting_around"]["ongoing"][0]


def test_street_event_without_notice_expects_roadblocks():
    parade = {"event_id": "visitphilly:halloween-parade", "source": "visitphilly", "event_type": "event",
              "title": "20th Annual Halloween Festival & Parade in Fairmount",
              "description": "2333 Fairmount Avenue, Philadelphia, PA", "start_date": "2026-10-25",
              "end_date": "2026-10-25", "start_ts": "2026-10-25T14:00:00-04:00", "end_ts": "2026-10-25T16:00:00-04:00",
              "seg_ids": []}
    show = {**parade, "event_id": "ticketmaster:osees", "source": "ticketmaster", "title": "Osees",
            "description": "Union Transfer, 1026 Spring Garden Street, Philadelphia, PA; Music"}
    exhibit = {**show, "event_id": "visitphilly:exhibit", "title": "A Museum Show", "end_date": "2027-03-01"}
    e, crowd = impact.brief([parade, show, exhibit], W0)["getting_around"]["expected"]
    assert e["impact"] == "roadblocks" and e["street"] == "Fairmount Ave"
    assert e["when"] == "Sun Oct 25, 2:00 PM – 4:00 PM" and e["cite"] == "[id:visitphilly:halloween-parade]"
    assert crowd["impact"] == "crowds" and crowd["street"] == "Spring Garden St"   # a show brings crowds and rideshares
    assert crowd["event_ids"] == ["ticketmaster:osees"]                           # a months-long exhibit does not
    notice = {"event_id": "event_closures:1", "source": "event_closures", "event_type": "event",
              "title": "Halloween Parade: Fairmount Ave", "start_ts": "2026-10-25T13:00:00-04:00",
              "end_ts": "2026-10-25T17:00:00-04:00", "seg_ids": []}
    assert "expected" not in impact.brief([parade, notice], W0)["getting_around"]   # the City's notice says it


def test_long_lane_work_on_main_streets_is_one_line(monkeypatch):
    monkeypatch.setattr(impact, "_seg_class", lambda seg_ids: 2)
    monkeypatch.setattr(impact, "_lane_work", lambda rows: {"event_ids": [r["event_id"] for r in rows]})
    rows = [permit(1, start="2026-10-02", end="2026-11-16"), permit(2, start="2026-09-28", end="2026-12-28")]
    b = impact.brief(rows, W0)
    assert b["getting_around"]["lane_work"]["event_ids"] == ["lane_closures:1", "lane_closures:2"]
    assert b["minor"]["count"] == 0                     # not counted twice


def test_area_outline_drops_records_just_outside(monkeypatch):
    monkeypatch.setattr(impact, "_seg_class", lambda seg_ids: 4)
    monkeypatch.setattr(impact, "_clip", lambda r, inside: None if r["event_id"].endswith(":1") else r)
    rows = [permit(1, "Full Closure", seg_ids=[10]), permit(2, "Full Closure", seg_ids=[20])]
    b = impact.brief(rows, W0, inside=frozenset({1}))
    assert [i["event_id"] for i in b["getting_around"]["new"]] == ["lane_closures:2"]
    assert b["minor"]["by_kind"] == {"work on streets just outside the area": 1}


def test_clip_keeps_boundary_streets_and_drops_the_neighbor():
    """Fairmount's outline runs along Poplar St; W Harper St (1900 block) is in Francisville."""
    from penn.geo import intersections, locate
    if not intersections.network().by_id:
        import pytest
        pytest.skip("no street centerline")
    inside = locate.area_inside("fairmount")
    poplar_2400, poplar_2200, harper_1900 = 420179, 420195, 421565
    r = permit(1, seg_ids=[poplar_2400, poplar_2200, harper_1900])
    assert impact._clip(r, inside)["seg_ids"] == [poplar_2400, poplar_2200]
    assert impact._clip(permit(2, seg_ids=[harper_1900]), inside) is None


def test_event_street():
    assert impact.event_street("2333 Fairmount Avenue, Philadelphia, PA") == "Fairmount Ave"
    assert impact.event_street("Theatre of Living Arts, 334 South St, Philadelphia, PA; Music") == "South St"
    assert impact.event_street("Field Day, 923 N. 2nd Street, Philadelphia, PA") == "N 2nd St"
    assert impact.event_street("6800 & 6900 blocks of Torresdale Avenue, Philadelphia, PA") == "Torresdale Ave"
    assert impact.event_street("Shofuso Japanese Cultural Center, Lansdowne & Horticultural drives") is None
