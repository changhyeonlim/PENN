import csv
import json
from datetime import datetime

import pytest

from penn.ingestion.press_releases import (
    Notice, build_csv, event_name, match_series, parse_item, parse_lead_in, parse_notice,
)

PUB = datetime(2026, 10, 15)
CATALOG = {"philly-bike-ride": "Philly Bike Ride", "no-kings-3": "No Kings 3 rally",
           "love-run": "Love Run Half Marathon", "philadelphia-marathon": "Philadelphia Marathon",
           "st-patricks-day-parade": "St. Patrick's Day Parade"}


@pytest.mark.parametrize("text, want", [
    ("The following streets will be closed to vehicular traffic beginning at 3:30 a.m. on Saturday, October 17, "
     "and will reopen no later than 2 p.m.:", ("2026-10-17", "", "03:30", "14:00")),
    ("The following streets will be closed starting at 6 a.m. until approximately noon on Sunday, November 22:",
     ("2026-11-22", "", "06:00", "12:00")),
    ("The following streets will be closed starting at 3 p.m. on Saturday, October 31 until approximately 1 a.m. "
     "on Sunday, November 1:", ("2026-10-31", "2026-11-01", "15:00", "01:00")),
    ("The following streets will be closed beginning at 7 a.m. on Saturday, October 17 and will reopen as the race "
     "concludes:", ("2026-10-17", "", "07:00", "")),
    ("The following streets will be closed from 10 a.m. to the conclusion of the parade on Sunday, October 18:",
     ("2026-10-18", "", "10:00", "")),
])
def test_lead_ins(text, want):
    li = parse_lead_in(text, PUB, None)
    assert (li.date, li.end_date, li.start, li.end) == want


def test_new_years_day_from_a_december_notice_is_next_year():
    li = parse_lead_in("The following streets will be closed beginning at 5 a.m. on January 1:", datetime(2025, 12, 18), None)
    assert li.date == "2026-01-01"


def test_lead_in_without_a_date_uses_the_event_date_or_gives_up():
    text = "The following streets will be closed from 6 a.m. to 2 p.m.:"
    assert parse_lead_in(text, PUB, datetime(2026, 10, 17)).date == "2026-10-17"
    assert parse_lead_in(text, PUB, None) is None


@pytest.mark.parametrize("text, street, a, b, notes", [
    ("Kelly Drive between Spring Garden Street and 25th Street", "Kelly Drive", "Spring Garden Street", "25th Street", ""),
    ("Eakins Oval in front of the Philadelphia Art Museum", "Eakins Oval", "", "", "in front of the Philadelphia Art Museum"),
    ("S. 22nd Street / N. 22nd Street between Christian Street and Benjamin Franklin Parkway",
     "22nd Street", "Christian Street", "Benjamin Franklin Parkway", ""),
    ("Market Street (eastbound lanes) from 15th Street to 18th Street;", "Market Street", "15th Street", "18th Street",
     "eastbound lanes"),
])
def test_items(text, street, a, b, notes):
    r = parse_item(text)
    assert (r["street"], r["from_street"], r["to_street"], r["notes"]) == (street, a, b, notes)


def test_conditional_items():
    assert parse_item("Arch Street between 16th Street and 15th Street (if needed)")["conditional"] == "yes"


@pytest.mark.parametrize("title, name", [
    ("City Announces Road Closures and Other Details for the 2026 Philly Bike Ride", "Philly Bike Ride"),
    ("City Announces Road Closures, Parking Restrictions and Other Details for SEIU Unions for All March",
     "SEIU Unions for All March"),
    ("City Announces Road Closures, Parking Restrictions and Other Details for “No Kings Protest” on "
     "Saturday, June 14, 2025", "No Kings Protest"),
    ("Traffic Advisory: Benjamin Franklin Parkway Road Closures Scheduled for November 1 – November 5", ""),
])
def test_event_names(title, name):
    assert event_name(title) == name


def test_series_matching():
    assert match_series("Philly Bike Ride", CATALOG) == "philly-bike-ride"
    assert match_series("Love Run Half Marathon", CATALOG) == "love-run"
    assert match_series("Philadelphia St. Patrick's Day Parade", CATALOG) == "st-patricks-day-parade"
    assert match_series("No Kings Protest", CATALOG) is None          # not the numbered "No Kings 3"
    assert match_series("OURFest Block Party and Festival", CATALOG) is None


PAGE = """<main><p>For immediate release: October 15, 2026</p>
<p>The City released road closure information for the 2026 Philly Bike Ride taking place on Saturday, October 17.</p>
<h4>Road Closures</h4>
<p>The following streets will be closed to vehicular traffic beginning at 3:30 a.m. on Saturday, October 17, and will reopen no later than 2 p.m.:</p>
<ul><li>Eakins Oval in front of the Philadelphia Art Museum</li></ul>
<ul><li>Kelly Drive between Spring Garden Street and 25th Street</li></ul>
<h4>Parking Restrictions</h4>
<p>Street parking on the following streets will be posted as Temporary No Parking zones from 12 a.m. to 1 p.m.:</p>
<ul><li>Arch Street between 16th Street and 15th Street</li></ul>
<p>###</p></main>"""


def notice(url="https://www.phila.gov/2026-10-15-bike/", file="2026-10-15-bike.html"):
    return Notice(id=1, title="City Announces Road Closures and Other Details for the 2026 Philly Bike Ride",
                  published="2026-10-15 10:00:00", updated_at="2026-10-15 10:00:00", url=url, file=file)


def test_notice_gives_closures_and_skips_parking():
    p = parse_notice(PAGE, notice(), CATALOG)
    assert p.problems == []
    assert [(r["series"], r["date"], r["street"], r["closure_start"], r["closure_end"]) for r in p.rows] == [
        ("philly-bike-ride", "2026-10-17", "Eakins Oval", "03:30", "14:00"),
        ("philly-bike-ride", "2026-10-17", "Kelly Drive", "03:30", "14:00"),
    ]


def test_build_leaves_curated_event_years_alone(tmp_path):
    snaps, closures = tmp_path / "snaps", tmp_path / "closures"
    snaps.mkdir(); closures.mkdir()
    n = notice()
    (snaps / n.file).write_text(PAGE, encoding="utf-8")
    (snaps / "index.json").write_text(json.dumps({"1": n.__dict__}), encoding="utf-8")
    rep = build_csv(snaps, closures, CATALOG)
    assert rep.rows == 2
    rows = list(csv.DictReader((closures / "press_releases.csv").open(encoding="utf-8")))
    assert {r["source_url"] for r in rows} == {n.url}

    # Once a curated file has the 2026 Bike Ride, the press rows step aside.
    (closures / "closures_2026.csv").write_text(
        "series,year,date,street,source_url\nphilly-bike-ride,2026,2026-10-17,Eakins Oval,https://www.phila.gov/other/\n",
        encoding="utf-8")
    rep = build_csv(snaps, closures, CATALOG)
    assert rep.rows == 0 and rep.curated


ADVISORY = """<main><p>For immediate release: October 16, 2026</p>
<h4>Traffic Advisory: Road Closures Scheduled for the October 17 Weekend</h4>
<p>There are several special events taking place on Saturday, October 17.</p>
<h4>Saturday, October 17</h4>
<p>Manayunk Fall Festival</p>
<p>11 a.m. – 4 p.m.</p>
<p>Celebrate the fall season at Manayunk's newest outdoor event.</p>
<p>The following streets will be closed from 8:30 a.m. to 5 p.m. on Saturday, October 17:</p>
<ul><li>Grape Street between Cresson Street and Main Street</li></ul>
<p>South Street: The Big Pivot Block Party Weekend</p>
<p>12 p.m. – 8 p.m.</p>
<p>The following streets will be closed from 7 a.m. to 10 p.m. on Saturday, October 17:</p>
<ul><li>South Street between Front Street and 8th Street</li></ul>
<p>###</p></main>"""


def test_advisory_names_each_event_from_the_line_above_its_hours():
    """Oct 16, 2026: the weekend advisory names each event in a plain line above its hours, not a heading, and
    every closure came out as "City Advisory 2026 10 16"."""
    n = Notice(id=2, title="Traffic Advisory: Road Closures Scheduled for the October 17 Weekend",
               published="2026-10-16 12:00:00", updated_at="2026-10-16 12:00:00",
               url="https://www.phila.gov/advisory/", file="advisory.html")
    p = parse_notice(ADVISORY, n, CATALOG)
    assert p.problems == []
    assert [(r["series"], r["street"]) for r in p.rows] == [
        ("manayunk-fall-festival", "Grape Street"),
        ("south-street-the-big-pivot-block-party-weekend", "South Street"),
    ]
