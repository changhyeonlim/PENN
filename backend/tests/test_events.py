from datetime import date

from penn.agent.events import editions, match_event, next_date, route_summary, thanksgiving

CAT = {
    "rocky-run": {"name": "Rocky Run 5K / 10 Mile Run/Walk", "aliases": "rocky run|the rocky race"},
    "broad-street-run": {"name": "Broad Street Run", "aliases": "broad street run|bsr"},
    "philadelphia-marathon": {"name": "Philadelphia Marathon", "aliases": "philadelphia marathon|the marathon"},
    "thanksgiving-day-parade": {"name": "Thanksgiving Day Parade", "aliases": "thanksgiving parade"},
}


def test_an_alias_or_name_in_the_question_finds_the_event():
    assert match_event("When is Rocky Run and where are the road closures?", CAT) == ["rocky-run"]
    assert match_event("is the marathon this weekend?", CAT) == ["philadelphia-marathon"]
    assert match_event("When is the Philadelphia Marathon this year?", CAT) == ["philadelphia-marathon"]


def test_a_street_name_is_not_the_race_named_after_it():
    assert match_event("any parade on Broad Street?", CAT) == []
    assert match_event("when is the broad street run", CAT) == ["broad-street-run"]


def test_an_event_not_in_the_catalog_matches_nothing():
    assert match_event("When is Christmas Village in Love Park?", CAT) == []


def test_rocky_run_fits_two_rules_that_disagree_for_2026():
    out = next_date([date(2024, 11, 9), date(2025, 11, 8)], date(2026, 10, 18))
    got = {e["rule"]: e["date"] for e in out["estimates"]}
    assert got == {"the second Saturday of November": "2026-11-14", "19 days before Thanksgiving": "2026-11-07"}
    assert out["announced"] is None and "note" in out


def test_a_rule_must_fit_half_the_years():
    sundays = [date(y, m, d) for y, m, d in ((2021, 11, 21), (2022, 11, 20), (2023, 11, 19), (2024, 11, 24), (2025, 11, 23))]
    out = next_date(sundays + [date(2016, 11, 19), date(2017, 11, 18)], date(2026, 10, 18))
    assert [e["rule"] for e in out["estimates"]] == ["4 days before Thanksgiving"]
    assert out["estimates"][0]["date"] == "2026-11-22"


def test_an_announced_date_wins_over_rules():
    out = next_date([date(2025, 11, 8), date(2026, 11, 14)], date(2026, 10, 18))
    assert out["announced"] == "Saturday, November 14, 2026" and out["estimates"] == []


def test_the_web_search_uses_the_name_people_use():
    from penn.agent.events import short_name
    assert short_name("rocky-run", "When is Rocky Run and where are the road closures?", CAT) == "Rocky Run"
    assert short_name("rocky-run", "is it soon?", CAT) == "Rocky Run"              # first alias when none was used
    assert short_name("philadelphia-marathon", "When is the Philadelphia Marathon?", CAT) == "Philadelphia Marathon"


def test_a_web_page_about_the_event_confirms_the_estimate_it_lists():
    from penn.agent.events import confirm_estimates
    ests = [{"rule": "19 days before Thanksgiving", "date": "2026-11-07"},
            {"rule": "the second Saturday of November", "date": "2026-11-14"}]
    pages = [{"event_id": "web:aaaa1111", "site": "rockyrun.com", "title": "Rocky Run - Race Info",
              "text": "Register Now Info November 14, Philadelphia, U.S.A."},
             {"event_id": "web:bbbb2222", "site": "phila.gov", "title": "Traffic Advisory",
              "text": "Rocky Run closures on November 7, 2025."},                 # another year: not a confirmation
             {"event_id": "web:cccc3333", "site": "example.org", "title": "Fall races",
              "text": "Something else on November 7."}]                         # does not name the event
    nxt = {"estimates": ests, "note": "Past dates fit different rules; they give different dates."}
    confirm_estimates(nxt, pages, "Rocky Run")
    (only,) = nxt["estimates"]
    assert only["date"] == "2026-11-14" and only["confirmed_by"] == [{"cite": "[id:web:aaaa1111]", "site": "rockyrun.com"}]
    assert [e["rule"] for e in nxt["also_fits"]] == ["19 days before Thanksgiving"] and "note" not in nxt


def test_thanksgiving():
    assert thanksgiving(2026) == date(2026, 11, 26) and thanksgiving(2025) == date(2025, 11, 27)


def _row(street, start, end, day="2025-11-08", event_id="event_closures:x", cells=(1, 2)):
    return {"closure_id": street, "year": int(day[:4]), "date": day, "street": street, "from_street": "A", "to_street": "B",
            "start": f"{day}T{start}:00-05:00", "end": f"{day}T{end}:00-05:00", "conditional": False,
            "source_url": "https://www.phila.gov/x", "geocoded": True, "event_id": event_id, "cells": list(cells)}


def test_an_edition_has_its_day_hours_cite_and_neighborhoods():
    rows = [_row("Benjamin Franklin Parkway", "03:00", "12:00"), _row("Kelly Drive", "06:00", "11:00", cells=(2, 3)),
            _row("Set-up Street", "18:00", "23:00", day="2025-11-07", event_id="")]
    (e,) = editions(rows, areas={"logan square": [1, 2], "east park": [3], "center city": [1, 2]})
    assert e["day"] == "Saturday, November 8, 2025" and e["closures_from"] == "3 AM" and e["reopen_by"] == "12 PM"
    assert e["cite"] == "[id:event_closures:x]" and e["closure_days"][0] == "Friday, November 7, 2025"
    assert e["neighborhoods"] == ["Logan Square"]                 # east park has one cell; center city is an alias
    r = route_summary(e)
    assert r["closes_first"].startswith("Benjamin Franklin Parkway") and r["main_streets"][0] == "Benjamin Franklin Parkway"


def test_an_edition_lists_every_placed_closure_so_one_cite_lights_the_route():
    rows = [_row("Kelly Drive", "06:00", "11:00", event_id="event_closures:a"),
            _row("MLK Drive", "06:00", "11:00", event_id="event_closures:b"),
            _row("Unplaced Ramp", "06:00", "11:00", event_id="")]
    (e,) = editions(rows, areas={})
    assert e["event_ids"] == ["event_closures:a", "event_closures:b"]
