from datetime import date, datetime, timezone

from penn.geo.streets import (EQUIVALENTS, Network, Segment, StreetName, normalize, resolve,
                              resolve_route, split_streets)
from penn.ingestion.event_closures import load_routes, to_event

# A small hand-made grid, 0.001 degrees (~100 m) per step. Broad Street runs north-south at x=0;
# cross streets run east-west. Coordinates are (lon, lat) offsets from a Philadelphia origin.
X0, Y0, D = -75.16, 39.95, 0.001


def pt(x, y):
    return (round(X0 + x * D, 6), round(Y0 + y * D, 6))


def seg(seg_id, pre, name, typ, a, b, lo=0, hi=0, suf=""):
    return Segment(seg_id, StreetName(pre, name, typ), [pt(*a), pt(*b)], lo, hi, suf)


SEGMENTS = [
    # N Broad St from Olney (y=0) north past Windrim (y=2) to Erie (y=3)
    seg(1, "N", "BROAD", "ST", (0, 0), (0, 1), 5600, 5699),
    seg(2, "N", "BROAD", "ST", (0, 1), (0, 2), 5500, 5599),
    seg(3, "N", "BROAD", "ST", (0, 2), (0, 3), 5400, 5499),
    seg(10, "W", "OLNEY", "AVE", (-1, 0), (0, 0)),
    seg(11, "E", "OLNEY", "AVE", (0, 0), (1, 0)),
    seg(12, "W", "WINDRIM", "AVE", (-1, 2), (0, 2)),
    seg(13, "W", "ERIE", "AVE", (-1, 3), (0, 3)),
    # Stops 50 m short of Broad Street: it never meets it.
    seg(14, "W", "LOUDON", "ST", (-1, 1), (-0.5, 1)),
    # South Street, east-west at y=-5, blocks 1500..1800
    seg(20, "", "SOUTH", "ST", (-5, -5), (-4, -5), 1500, 1599),
    seg(21, "", "SOUTH", "ST", (-4, -5), (-3, -5), 1600, 1699),
    seg(22, "", "SOUTH", "ST", (-3, -5), (-2, -5), 1700, 1799),
    seg(23, "", "SOUTH", "ST", (-2, -5), (-1, -5), 1800, 1899),
    # 5th Street, interrupted by Independence Mall East between Market (y=10) and Arch (y=12)
    seg(30, "S", "5TH", "ST", (5, 8), (5, 10)),
    seg(31, "N", "INDEPENDENCE", "MALL", (5, 10), (5, 12), suf="E"),
    seg(32, "", "MARKET", "ST", (4, 10), (5, 10)),
    seg(33, "", "ARCH", "ST", (4, 12), (5, 12)),
    seg(34, "", "WALNUT", "ST", (4, 8), (5, 8)),
    # Larchwood is an avenue in the centerline; notices call it a street
    seg(40, "S", "51ST", "ST", (8, 0), (8, 1)),
    seg(41, "", "LARCHWOOD", "AVE", (7, 0), (8, 0)),
    seg(42, "", "PINE", "ST", (7, 1), (8, 1)),
]


def net(**kw):
    return Network(SEGMENTS, **kw)


def ids(res):
    return sorted(s.seg_id for s in res.segments)


# Name normalization


def test_normalize_types_and_directions():
    assert normalize("North Broad Street") == StreetName("N", "BROAD", "ST")
    assert normalize("N. Broad St.") == StreetName("N", "BROAD", "ST")
    assert normalize("Washington Ave.") == StreetName("", "WASHINGTON", "AVE")
    assert normalize("S. Penn Square") == StreetName("S", "PENN", "SQ")


def test_normalize_direction_word_that_is_the_name():
    assert normalize("South Street") == StreetName("", "SOUTH", "ST")


def test_normalize_aliases():
    parkway = StreetName("", "BENJAMIN FRANKLIN", "PKWY")
    assert normalize("Benjamin Franklin Parkway") == parkway
    assert normalize("Ben Franklin Parkway") == parkway
    jfk = StreetName("", "JOHN F KENNEDY", "BLVD")
    assert normalize("John F. Kennedy Boulevard") == jfk
    assert normalize("JFK Blvd") == jfk
    assert normalize("John F. Kennedy Blvd.") == jfk
    mlk = StreetName("", "MARTIN LUTHER KING", "DR")
    assert normalize("MLK Drive") == mlk
    assert normalize("Martin Luther King Jr. Drive") == mlk


def test_normalize_strips_lane_notes():
    assert normalize("18th Street (north side)") == StreetName("", "18TH", "ST")
    assert normalize("Benjamin Franklin Parkway (inner lanes)").name == "BENJAMIN FRANKLIN"
    assert normalize("Center lanes of Benjamin Franklin Parkway (both directions)").name == "BENJAMIN FRANKLIN"
    assert normalize("Kelly Drive inbound lane") == StreetName("", "KELLY", "DR")
    assert normalize("Inbound Spring Garden Street") == StreetName("", "SPRING GARDEN", "ST")


def test_space_insensitive_key():
    assert normalize("Waterworks Drive").key == StreetName("", "WATER WORKS", "DR").key


def test_split_plural_street_lists():
    assert split_streets("16th, 17th, and 18th Streets") == ["16th Street", "17th Street", "18th Street"]
    assert split_streets("16th and 17th Streets") == ["16th Street", "17th Street"]
    assert split_streets("Broad Street") == ["Broad Street"]


# Intersections and from-to chains


def test_from_to_chain_between_two_intersections():
    res = resolve({"street": "Broad Street", "from_street": "Olney Avenue", "to_street": "Windrim Avenue"}, net())
    assert res.ok and res.kind == "from_to"
    assert ids(res) == [1, 2]


def test_from_to_chain_order_does_not_matter():
    res = resolve({"street": "Broad Street", "from_street": "Erie Avenue", "to_street": "Windrim Avenue"}, net())
    assert ids(res) == [3]


def test_intersection_is_a_shared_endpoint():
    n = net()
    olney = n.find(normalize("Olney Avenue"))
    broad = n.find(normalize("Broad Street"))
    shared = {e for s in broad for e in s.ends} & {e for s in olney for e in s.ends}
    assert shared == {pt(0, 0)}


def test_streets_that_do_not_touch_are_not_an_intersection():
    res = resolve({"street": "Broad Street", "from_street": "Loudon Street", "to_street": "Erie Avenue"}, net())
    assert not res.ok
    assert "does not meet" in res.reason


# Block ranges


def test_block_range_uses_address_ranges():
    res = resolve({"street": "1600-1700 blocks of South Street", "from_street": "", "to_street": ""}, net())
    assert res.kind == "block_range"
    assert ids(res) == [21, 22]


def test_single_block_and_mis_encoded_dash():
    assert ids(resolve({"street": "1800 block of South Street"}, net())) == [23]
    assert ids(resolve({"street": "1600�1700 blocks of South Street"}, net())) == [21, 22]


# Unresolvable rows


def test_unknown_place_is_left_unresolved():
    res = resolve({"street": "Eakins Oval", "from_street": "", "to_street": ""}, net())
    assert not res.ok and res.kind == "named"
    assert res.reason == "no centerline segment named EAKINS OVAL"


def test_endpoint_that_is_not_a_street_is_left_unresolved():
    res = resolve({"street": "Broad Street", "from_street": "Erie Avenue", "to_street": "Navy Yard"}, net())
    assert not res.ok
    assert "NAVY YARD" in res.reason


def test_one_end_missing_and_areas():
    assert resolve({"street": "Juniper Street", "from_street": "", "to_street": "Market Street"}, net()).kind == "one_end"
    area = resolve({"street": "All roads", "from_street": "Arch Street to Spring Garden Street",
                    "to_street": "18th Street to 22nd Street"}, net())
    assert area.kind == "area" and not area.ok


# Optional name rules


def test_independence_mall_equivalent_is_opt_in():
    row = {"street": "5th Street", "from_street": "Walnut Street", "to_street": "Arch Street"}
    assert not resolve(row, net()).ok
    res = resolve(row, net(equivalents=EQUIVALENTS))
    assert ids(res) == [30, 31]
    assert res.notes == ["included N INDEPENDENCE MALL E as 5TH ST"]


def test_equivalent_not_noted_when_chain_does_not_use_it():
    # 5th Street south of Walnut never touches the Mall segment.
    extra = [seg(35, "S", "5TH", "ST", (5, 6), (5, 8)), seg(36, "", "LOCUST", "ST", (4, 6), (5, 6))]
    n = Network(SEGMENTS + extra, equivalents=EQUIVALENTS)
    res = resolve({"street": "5th Street", "from_street": "Locust Street", "to_street": "Walnut Street"}, n)
    assert ids(res) == [35]
    assert res.notes == []


def test_loose_type_only_in_chains_and_noted():
    row = {"street": "51st Street", "from_street": "Larchwood Street", "to_street": "Pine Street"}
    assert not resolve(row, net()).ok
    res = resolve(row, net(loose_types=True))
    assert ids(res) == [40]
    assert res.notes == ["read LARCHWOOD ST as LARCHWOOD AVE"]


# Events


def closure(**over):
    r = {"closure_id": "broad-street-run-2026-05-03-abc", "series": "broad-street-run", "year": 2026,
         "start_date": date(2026, 5, 3), "end_date": date(2026, 5, 3), "street": "Broad Street",
         "from_street": "Olney Avenue", "to_street": "Windrim Avenue",
         "start_ts": datetime(2026, 5, 3, 10, tzinfo=timezone.utc),
         "end_ts": datetime(2026, 5, 3, 16, tzinfo=timezone.utc),
         "conditional": False, "notes": "", "source_url": "https://www.phila.gov/x/"}
    r.update(over)
    return r


def test_event_fields_and_certainty():
    segs = resolve(closure(), net()).segments
    before = to_event(closure(), segs, [], datetime(2026, 5, 1, tzinfo=timezone.utc))
    after = to_event(closure(), segs, [], datetime(2026, 5, 4, tzinfo=timezone.utc))
    assert before.claim_certainty == "scheduled" and after.claim_certainty == "observed"
    assert before.source == "event_closures" and before.event_type == "event"
    assert before.source_authority == "curated"
    assert before.event_id == "event_closures:broad-street-run-2026-05-03-abc"
    assert before.geometry_wkt.startswith("MULTILINESTRING")
    assert before.h3_cells and before.h3_r9
    assert "1, 2" in before.description


def test_no_end_time_uses_end_of_end_date():
    segs = resolve(closure(), net()).segments
    row = closure(end_ts=None)
    assert to_event(row, segs, [], datetime(2026, 5, 3, 23, tzinfo=timezone.utc)).claim_certainty == "scheduled"
    assert to_event(row, segs, [], datetime(2026, 5, 4, 5, tzinfo=timezone.utc)).claim_certainty == "observed"


# Direction words that are part of the name


def test_direction_word_inside_a_name():
    access = seg(50, "", "NORTH ACCESS", "RD", (0, 3), (1, 3))
    n = Network(SEGMENTS + [access])
    res = resolve({"street": "Broad Street", "from_street": "Windrim Avenue", "to_street": "North Access Road"}, n)
    assert ids(res) == [3]


# Curated routes


LEGS = [
    {"street": "Broad Street", "from_street": "Olney Avenue", "to_street": "Windrim Avenue"},
    {"street": "Broad Street", "from_street": "Windrim Avenue", "to_street": "Erie Avenue"},
]


def test_route_chains_every_leg_in_order():
    res = resolve_route(LEGS, net(), note="Course supplied by the team.")
    assert res.kind == "route" and ids(res) == [1, 2, 3]
    assert res.notes == ["Course supplied by the team."]


def test_route_with_a_bad_leg_is_unresolved_whole():
    legs = LEGS + [{"street": "Broad Street", "from_street": "Erie Avenue", "to_street": "Navy Yard"}]
    res = resolve_route(legs, net())
    assert not res.ok
    assert res.reason.startswith("route leg 3 (Broad Street): ")


ROUTE_HEADER = "closure_id,leg,street,from_street,to_street,note\n"


def test_load_routes_orders_legs(tmp_path):
    p = tmp_path / "routes.csv"
    p.write_text(ROUTE_HEADER
                 + "c1,2,Broad Street,Windrim Avenue,Erie Avenue,\n"
                 + "c1,1,Broad Street,Olney Avenue,Windrim Avenue,From the race map\n")
    routes, errors = load_routes(p)
    assert errors == []
    assert [leg["from_street"] for leg in routes["c1"]["legs"]] == ["Olney Avenue", "Windrim Avenue"]
    assert routes["c1"]["note"] == "From the race map"


def test_load_routes_rejects_gaps_and_missing_note(tmp_path):
    p = tmp_path / "routes.csv"
    p.write_text(ROUTE_HEADER
                 + "gap,1,Broad Street,Olney Avenue,Windrim Avenue,note\n"
                 + "gap,3,Broad Street,Windrim Avenue,Erie Avenue,\n"
                 + "nonote,1,Broad Street,Olney Avenue,Windrim Avenue,\n")
    routes, errors = load_routes(p)
    assert routes == {}
    reasons = " | ".join(e.reason for e in errors)
    assert "legs [1, 3] are not 1..2" in reasons
    assert "nonote: no note" in reasons


def test_missing_routes_file_is_fine(tmp_path):
    assert load_routes(tmp_path / "absent.csv") == ({}, [])


def test_eakins_oval_is_the_whole_oval():
    from penn.geo.streets import PLACES, Network, Segment, StreetName, resolve
    ids = PLACES["EAKINSOVAL"]
    segs = [Segment(i, StreetName("", "BENJAMIN FRANKLIN", "PKWY"), [(0, k), (0, k + 1)]) for k, i in enumerate(ids)]
    net = Network(segs)
    for row in ({"street": "Eakins Oval", "from_street": "", "to_street": ""},
                {"street": "Eakins Oval", "from_street": "Kelly Drive", "to_street": "Martin Luther King Jr. Drive"}):
        r = resolve(row, net)
        assert r.ok and sorted(s.seg_id for s in r.segments) == sorted(ids), row
