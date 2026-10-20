import json
from datetime import date, time

import pytest

from penn.ingestion.visitphilly import PAGE_URL, Span, Unparsed, in_city, load_all, parse_dates, parse_times, to_events

SEEN = date(2026, 10, 15)
CITY_HALL = {"lat": 39.9526, "lng": -75.1636}


@pytest.mark.parametrize("text, spans", [
    ("Saturday, October 17, 2026 | 7 a.m.", [Span(date(2026, 10, 17), date(2026, 10, 17), time(7, 0))]),
    ("Saturday, October 17, 2026 | 10 a.m. - 4 p.m.",
     [Span(date(2026, 10, 17), date(2026, 10, 17), time(10, 0), time(16, 0))]),
    ("Friday, October 16, 2026 | 7:30-11 p.m.",
     [Span(date(2026, 10, 16), date(2026, 10, 16), time(19, 30), time(23, 0))]),
    ("Sunday, October 18, 2026 | Noon-5 p.m.",
     [Span(date(2026, 10, 18), date(2026, 10, 18), time(12, 0), time(17, 0))]),
    ("October 15-17, 2026", [Span(date(2026, 10, 15), date(2026, 10, 17))]),
    ("October 14 - November 7, 2026", [Span(date(2026, 10, 14), date(2026, 11, 7))]),
    ("October 15, 2026 - May 13, 2027", [Span(date(2026, 10, 15), date(2027, 5, 13))]),
    ("October 16, 2026 – October 2027", [Span(date(2026, 10, 16), date(2027, 10, 31))]),
    ("October 13 & 17, 2026", [Span(date(2026, 10, 13), date(2026, 10, 13)),
                               Span(date(2026, 10, 17), date(2026, 10, 17))]),
    ("Through Saturday, October 17, 2026", [Span(SEEN, date(2026, 10, 17))]),
])
def test_date_lines(text, spans):
    assert parse_dates(text, SEEN)[0] == spans


def test_through_keeps_its_qualifier():
    spans, q = parse_dates("Through Sunday, October 25, 2026 (Sundays only)", SEEN)
    assert spans == [Span(SEEN, date(2026, 10, 25))] and q == "Sundays only"


@pytest.mark.parametrize("text", ["Dates vary by event", "Ongoing", "Now open", "",
                                  "Through mid-2027 (dates vary by museum)"])
def test_lines_without_dates_are_refused(text):
    with pytest.raises(Unparsed):
        parse_dates(text, SEEN)


def test_start_time_takes_the_end_meridiem_unless_that_inverts_the_range():
    assert parse_times("1-3 p.m.") == (time(13, 0), time(15, 0))
    assert parse_times("11-1 p.m.") == (time(11, 0), time(13, 0))
    assert parse_times("8 p.m.") == (time(20, 0), None)


def test_city_line():
    assert in_city(CITY_HALL["lng"], CITY_HALL["lat"], "City Hall, 1400 John F. Kennedy Boulevard, Philadelphia, PA")
    assert in_city(CITY_HALL["lng"], CITY_HALL["lat"])
    assert not in_city(-75.3877, 39.9168)                                     # Media, PA, no address
    # A town other than Philadelphia is refused even with a pin inside the city.
    assert not in_city(CITY_HALL["lng"], CITY_HALL["lat"], "1 Harbour Boulevard, Camden, NJ")


def item(**over):
    it = {"textid": "test-fest", "title": "Test Fest", "link": "https://example.org/fest",
          "date_text": "Saturday, October 17, 2026 | Noon-4 p.m.",
          "where": "Franklin Square, 200 N. 6th Street, Philadelphia, PA",
          "location": {"lat": 39.9556, "lng": -75.1503}, "other_locations": []}
    it.update(over)
    return it


def test_item_becomes_a_cited_curated_event():
    (e,) = to_events(item(), SEEN, "2026-10-15")
    assert e.event_id == "visitphilly:test-fest-2026"
    assert e.source_url == PAGE_URL + "#test-fest" and e.source_authority == "curated"
    assert e.geometry_wkt.startswith("POINT") and e.h3_cells
    assert e.start_ts.hour == 12 and e.end_ts.hour == 16
    assert e.description == "Franklin Square, 200 N. 6th Street, Philadelphia, PA"
    assert e.organization == "visitphilly.com, updated 2026-10-15"


def test_links_to_its_visitphilly_article_never_the_organizer():
    article = "https://www.visitphilly.com/articles/philadelphia/test-fest/"
    (e,) = to_events(item(link="https://example.org/fest", read_more=article), SEEN, None)
    assert e.source_url == article
    (e,) = to_events(item(link=PAGE_URL + "#other", read_more="https://example.org/tickets"), SEEN, None)
    assert e.source_url == PAGE_URL + "#test-fest"     # no article: its heading on the weekly page


def test_qualifier_rides_with_the_place():
    (e,) = to_events(item(date_text="Through Sunday, October 25, 2026 (Sundays only)"), SEEN, None)
    assert e.description.endswith("; Sundays only")


def test_separate_dates_become_separate_rows():
    evs = to_events(item(date_text="October 13 & 17, 2026"), SEEN, None)
    assert [e.source_record_id for e in evs] == ["test-fest-2026-1", "test-fest-2026-2"]
    assert [e.start_date.day for e in evs] == [13, 17]


def test_extra_locations_outside_the_city_are_dropped():
    others = [{"name": "Bok Bar", "address": "800 Mifflin Street", "city": "", "lat": 39.9254, "lng": -75.1596},
              {"name": "Elsewhere", "address": "1 Main St", "city": "Glenside", "lat": 40.1023, "lng": -75.1524}]
    (e,) = to_events(item(other_locations=others), SEEN, None)
    assert e.geometry_wkt.startswith("MULTIPOINT") and e.geometry_wkt.count("(") == 3   # 2 points


def test_outside_item_yields_nothing():
    assert to_events(item(where="1 Harbour Boulevard, Camden, NJ", location={"lat": 39.9425, "lng": -75.1265}),
                     SEEN, None) == []


def test_later_snapshot_wins(tmp_path):
    def snap(name, fetched, text):
        (tmp_path / name).write_text(json.dumps({"fetched_at": fetched, "last_updated": fetched[:10],
                                                 "items": [item(date_text=text)]}), encoding="utf-8")
    snap("2026-10-13T0900.json", "2026-10-13T09:00:00-04:00", "Saturday, October 17, 2026 | 11 a.m.")
    snap("2026-10-15T0900.json", "2026-10-15T09:00:00-04:00", "Saturday, October 17, 2026 | Noon")
    events, rep, raw = load_all(tmp_path)
    assert rep.files == 2 and rep.items == 1 and len(events) == 1
    assert events[0].start_ts.hour == 12
