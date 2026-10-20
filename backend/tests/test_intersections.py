import httpx
from penn.geo import intersections, locate
from penn.geo.streets import Network, Segment, StreetName


def seg(i, d, name, typ, a, b):
    return Segment(i, StreetName(d, name, typ), [a, b])


# A tiny grid: S 12TH ST runs north-south and crosses WALNUT ST at (0, 0).
# WALNUT LN exists elsewhere and never meets 12th; FAIRMOUNT AVE crosses 12th at (0, 1).
NET = Network([
    seg(1, "S", "12TH", "ST", (0.0, -0.001), (0.0, 0.0)),
    seg(2, "S", "12TH", "ST", (0.0, 0.0), (0.0, 0.001)),
    seg(3, "", "WALNUT", "ST", (-0.001, 0.0), (0.0, 0.0)),
    seg(4, "", "WALNUT", "ST", (0.0, 0.0), (0.001, 0.0)),
    seg(5, "", "WALNUT", "LN", (0.5, 0.5), (0.501, 0.5)),
    seg(6, "S", "12TH", "ST", (0.0, 0.001), (0.0, 0.01)),
    seg(7, "", "FAIRMOUNT", "AVE", (-0.001, 0.01), (0.0, 0.01)),
])


def test_finds_corner_inside_a_question():
    hit = intersections.find("What street work is happening near 12th and Walnut on Saturday?", NET)
    assert hit.label == "S 12th St & Walnut St"
    assert hit.seg_ids == [1, 2, 3, 4]          # the four legs, not WALNUT LN
    assert hit.node == (0.0, 0.0)


def test_connectors_and_suffixes():
    for q in ["Walnut & 12th", "Walnut St at 12th Street", "anything at walnut and 12th?"]:
        assert intersections.find(q, NET).seg_ids == [1, 2, 3, 4], q


def test_no_intersection_when_streets_do_not_meet():
    assert intersections.find("Walnut Lane and Fairmount", NET) is None
    assert intersections.find("no connector here", NET) is None


def test_names_crossing_twice_far_apart_are_ambiguous():
    net = Network([seg(1, "", "A", "ST", (0, 0), (0, 0.01)), seg(2, "", "B", "ST", (0, 0), (0.01, 0.01)),
                   seg(3, "", "B", "ST", (0.01, 0.01), (0, 0.01))])
    assert intersections.find("A and B", net) is None   # they meet at both ends, ~1 km apart


def test_intersection_beats_neighborhood_name(monkeypatch):
    monkeypatch.setattr(intersections, "network", lambda: NET)
    monkeypatch.setattr(locate, "_AREAS", {"fairmount": [1, 2, 3]})
    r = locate.from_text("any street work at 12th and Fairmount?")
    assert r.method == "intersection" and r.seg_ids == [6, 7]
    assert locate.from_text("events in Fairmount this weekend").method == "area"
    no_ais = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(404)))
    assert locate.from_text("my slogan is fairmountain", client=no_ais).method == "none"   # whole words only


def test_leading_article_on_second_street():
    assert intersections.find("anything near 12th and the Walnut Street corner?", NET).seg_ids == [1, 2, 3, 4]


def test_legs_extend_to_the_whole_block():
    # Locust is split at an alley: 1300-1323 touches the corner, 1324-1399 is the rest of the block.
    net = Network([
        Segment(1, StreetName("S", "13TH", "ST"), [(0.0, -0.001), (0.0, 0.0)], 200, 299),
        Segment(2, StreetName("", "LOCUST", "ST"), [(0.0, 0.0), (0.001, 0.0)], 1300, 1323),
        Segment(3, StreetName("", "LOCUST", "ST"), [(0.001, 0.0), (0.002, 0.0)], 1324, 1399),
        Segment(4, StreetName("", "LOCUST", "ST"), [(0.002, 0.0), (0.003, 0.0)], 1400, 1499),
    ])
    assert intersections.find("13th and Locust", net).seg_ids == [1, 2, 3]   # not the 1400 block
