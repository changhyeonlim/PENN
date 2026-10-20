from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from penn.ingestion import ticketmaster as tm

PUNCH_LINE = {"name": "Punch Line Philly", "address": {"line1": "33 E. Laurel Street"},
              "city": {"name": "Philadelphia"}, "state": {"stateCode": "PA"},
              "location": {"latitude": "39.9655", "longitude": "-75.1368"}}
GLENSIDE = {"name": "Keswick Theatre", "address": {"line1": "291 N. Keswick Ave"}, "city": {"name": "Glenside"},
            "state": {"stateCode": "PA"}, "location": {"latitude": "40.1032", "longitude": "-75.1517"}}


def event(**over):
    e = {"id": "vv17FZ_aGkSmM0x3", "name": "Big Wig  Brunch", "url": "https://www.ticketmaster.com/event/0200649CEEDE4892",
         "type": "event", "test": False,
         "dates": {"start": {"localDate": "2026-10-17", "localTime": "12:00:00", "dateTime": "2026-10-17T16:00:00Z"},
                   "status": {"code": "onsale"}},
         "classifications": [{"primary": True, "segment": {"name": "Arts & Theatre"}}],
         "_embedded": {"venues": [PUNCH_LINE]}}
    e.update(over)
    return e


def test_event_becomes_a_listing_at_its_venue():
    e = tm.to_event(event())
    assert e.event_id == "ticketmaster:vv17FZ_aGkSmM0x3"
    assert e.title == "Big Wig Brunch" and e.organization == "Ticketmaster"
    assert e.source_url == "https://www.ticketmaster.com/event/0200649CEEDE4892"
    assert e.geometry_wkt == "POINT (-75.136800 39.965500)" and e.h3_cells == [e.h3_r9]
    assert e.start_ts == datetime(2026, 10, 17, 16, tzinfo=timezone.utc) and e.end_ts is None
    assert e.description == "Punch Line Philly, 33 E. Laurel Street, Philadelphia, PA; Arts & Theatre"


def test_time_to_be_announced_keeps_only_the_date():
    st = {"localDate": "2026-11-02", "dateTime": "2026-11-02T04:00:00Z", "timeTBA": True}
    e = tm.to_event(event(dates={"start": st, "status": {"code": "onsale"}}))
    assert e.start_ts is None and str(e.start_date) == "2026-11-02" == str(e.end_date)


def test_postponed_says_so_and_undefined_segment_is_dropped():
    e = tm.to_event(event(dates={**event()["dates"], "status": {"code": "postponed"}},
                          classifications=[{"primary": True, "segment": {"name": "Undefined"}}]))
    assert e.description.endswith("Philadelphia, PA; Postponed") and e.status == "postponed"


@pytest.mark.parametrize("name", ["Post Game Slap Shot - MUST HAVE TICKET TO GAME", "Suites - JOURNEY - Final Frontier Tour",
                                  "Eagles vs. Giants Parking", "VIP Pregame Package"])
def test_add_ons_are_not_events(name):
    rep = tm.FetchReport()
    assert tm.to_events([event(name=name)], rep) == [] and rep.add_ons == [name]


def test_a_show_named_like_a_word_in_the_add_on_list_stays():
    assert tm.to_events([event(name="Philadelphia Flyers vs. Ottawa Senators")])


def test_cancelled_outside_and_unplaced_events_are_left_out():
    rep = tm.FetchReport()
    raw = [event(id="a"), event(id="b", dates={**event()["dates"], "status": {"code": "cancelled"}}),
           event(id="c", _embedded={"venues": [GLENSIDE]}),
           event(id="d", _embedded={"venues": [{"name": "TBA"}]}), event(id="e", test=True)]
    out = tm.to_events(raw, rep)
    assert [e.source_record_id for e in out] == ["a"]
    assert len(rep.skipped_status) == 1 and len(rep.outside) == 1 and len(rep.no_location) == 1


class FakeAIS:
    """The City's address search: 1100 Pattison Ave is in the stadium complex."""

    def __init__(self, points):
        self.points, self.calls = points, []

    def get(self, url, params=None):
        self.calls.append(url)
        hit = next((p for a, p in self.points.items() if a.lower() in url.lower()), None)
        body = {"features": [{"geometry": {"coordinates": list(hit)}}] if hit else []}
        return SimpleNamespace(status_code=200, json=lambda: body)


STATESIDE = {"name": "Stateside Live Philadelphia", "address": {"line1": "1100 Pattison Ave"},
             "city": {"name": "Philadelphia"}, "state": {"stateCode": "PA"},
             "location": {"latitude": "39.910999", "longitude": "-75.153397"}}   # 1.5 km off, as Ticketmaster has it


def test_a_venue_pin_far_from_its_address_moves_to_the_citys_point():
    import copy
    raw = [event(id="a", name="Afrojack (18 and Over)", _embedded={"venues": [copy.deepcopy(STATESIDE)]}),
           event(id="b", name="Another Night", _embedded={"venues": [copy.deepcopy(STATESIDE)]})]
    ais = FakeAIS({"1100 Pattison Ave": (-75.1690215, 39.9045221)})
    rep = tm.FetchReport()
    tm.correct_venues(raw, client=ais, rep=rep, pace=0)
    assert len(ais.calls) == 1                                  # one lookup per address
    assert rep.moved == ["Stateside Live Philadelphia: 1515 m"]
    e = tm.to_event(raw[0])
    assert (round(e.centroid_lat, 4), round(e.centroid_lon, 4)) == (39.9045, -75.169)


def test_a_pin_close_to_its_address_moves_to_the_same_point_but_is_not_reported():
    raw = [event()]
    rep = tm.FetchReport()
    tm.correct_venues(raw, client=FakeAIS({"33 E. Laurel Street": (-75.13690, 39.96545)}), rep=rep, pace=0)
    assert _venue_loc(raw[0]) == {"latitude": "39.96545", "longitude": "-75.1369"} and rep.moved == []


def test_a_rate_limited_lookup_is_retried(monkeypatch):
    monkeypatch.setattr(tm.clock, "sleep", lambda s: None)
    replies = iter([SimpleNamespace(status_code=429, headers={"Retry-After": "1"}, json=lambda: {}),
                    SimpleNamespace(status_code=200, headers={}, json=lambda: {"features": [{"geometry": {"coordinates": [-75.169, 39.9045]}}]})])
    assert tm.ais_point("1100 Pattison Ave", SimpleNamespace(get=lambda url, params=None: next(replies))) == (-75.169, 39.9045)


def test_no_address_match_keeps_the_pin():
    raw = [event()]
    tm.correct_venues(raw, client=FakeAIS({}), rep=tm.FetchReport(), pace=0)
    assert _venue_loc(raw[0]) == PUNCH_LINE["location"]


def _venue_loc(e):
    return e["_embedded"]["venues"][0]["location"]


def test_a_performance_under_two_venue_records_is_kept_once():
    rep = tm.FetchReport()
    out = tm.to_events([event(id="a"), event(id="b")], rep)   # same title, time and place
    assert [e.source_record_id for e in out] == ["a"] and rep.duplicates == 1


def test_a_performance_titled_differently_by_its_second_record_is_kept_once():
    rep = tm.FetchReport()
    out = tm.to_events([event(id="a", name="Walnut Street Theatre's THE ADDAMS FAMILY: The Musical"),
                        event(id="b", name="The Addams Family (Touring)"),
                        event(id="c", name="A Different Show")], rep)      # same time and place, other title
    assert [e.source_record_id for e in out] == ["a", "c"] and rep.duplicates == 1


def test_dates_only_the_second_record_lists_take_the_first_records_title():
    later = {**event()["dates"], "start": {"localDate": "2026-10-23", "dateTime": "2026-10-23T23:30:00Z"}}
    out = tm.to_events([event(id="a", name="The Addams Family (Touring)"),
                        event(id="b", name="Walnut Street Theatre's THE ADDAMS FAMILY: The Musical"),     # same night
                        event(id="c", name="Walnut Street Theatre's THE ADDAMS FAMILY: The Musical", dates=later)])
    assert [(e.source_record_id, e.title) for e in out] == [("a", "The Addams Family (Touring)"),
                                                            ("c", "The Addams Family (Touring)")]


class FakeClient:
    """Answers like the Discovery API: totalElements depends on the window, pages of PAGE_SIZE."""

    def __init__(self, per_day: int):
        self.per_day, self.calls = per_day, []

    def get(self, url, params):
        self.calls.append(params)
        a = datetime.fromisoformat(params["startDateTime"].replace("Z", "+00:00"))
        b = datetime.fromisoformat(params["endDateTime"].replace("Z", "+00:00"))
        total = round(self.per_day * (b - a) / timedelta(days=1))
        n, size = params["page"], params["size"]
        ids = [f"{a:%m%d%H}-{i}" for i in range(n * size, min(total, (n + 1) * size))]
        body = {"page": {"totalElements": total, "totalPages": -(-total // size)},
                "_embedded": {"events": [event(id=i) for i in ids]}}
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setattr(tm, "settings", SimpleNamespace(ticketmaster_api_key="test-key", ais_gatekeeper_key=""))


def test_a_window_under_the_deep_paging_limit_is_read_page_by_page():
    c = FakeClient(per_day=10)                      # 600 in 60 days: 3 pages
    raw = tm.fetch(datetime(2026, 10, 17, tzinfo=timezone.utc), days=60, client=c)
    assert len(raw) == 600 and len(c.calls) == 3


def test_a_window_over_the_limit_is_split_until_each_part_fits():
    c = FakeClient(per_day=25)                      # 1,500 in 60 days: split into two 750s
    raw = tm.fetch(datetime(2026, 10, 17, tzinfo=timezone.utc), days=60, client=c)
    assert len(raw) == 1500
    assert all((p["page"] + 1) * p["size"] <= tm.DEEP_LIMIT for p in c.calls)


def test_no_key_means_no_calls(monkeypatch):
    monkeypatch.setattr(tm, "settings", SimpleNamespace(ticketmaster_api_key="", ais_gatekeeper_key=""))
    with pytest.raises(tm.NoKey):
        tm.fetch(datetime(2026, 10, 17, tzinfo=timezone.utc), client=FakeClient(1))
