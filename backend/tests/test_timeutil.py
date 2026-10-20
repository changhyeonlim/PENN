from datetime import date, datetime

from penn.config import LOCAL_TZ
from penn.timeutil import esri_date_to_calendar_date, is_open_ended, resolve_window

# 2026-10-08T00:00:00Z, the way the Lane Closures layer stores "Oct 8"
OCT_8_UTC_MIDNIGHT_MS = 1791417600000


def test_esri_date_keeps_calendar_day():
    # Converting to Eastern time first would give Oct 7 (8 PM). The calendar date must stay Oct 8.
    assert esri_date_to_calendar_date(OCT_8_UTC_MIDNIGHT_MS) == date(2026, 10, 8)


def test_open_ended_placeholder():
    assert is_open_ended(date(2024, 1, 15), date(2103, 1, 19))
    assert not is_open_ended(date(2025, 5, 20), date(2026, 12, 4))


NOW = datetime(2026, 10, 4, 21, 0, tzinfo=LOCAL_TZ)  # a Sunday evening


def test_tomorrow_morning():
    w = resolve_window("can I park here tomorrow morning?", NOW)
    assert w.start == datetime(2026, 10, 5, 6, 0, tzinfo=LOCAL_TZ)
    assert w.end == datetime(2026, 10, 5, 12, 0, tzinfo=LOCAL_TZ)


def test_saturday_is_next_saturday():
    w = resolve_window("Will this area be hard to drive through Saturday morning?", NOW)
    assert w.start.date() == date(2026, 10, 10)
    assert "morning" in w.label


def test_this_weekend_is_two_days():
    w = resolve_window("what's happening this weekend", NOW)
    assert w.dates() == [date(2026, 10, 10), date(2026, 10, 11)]


def test_no_time_phrase():
    assert resolve_window("why is this street closed", NOW) is None


def test_explicit_dates():
    from datetime import datetime
    from penn.timeutil import resolve_window
    now = datetime(2026, 10, 13, 12, tzinfo=LOCAL_TZ)
    for q, want in [("Will the marathon close streets on November 22?", "2026-11-22"),
                    ("anything on Nov. 26th, 2026 morning", "2026-11-26"),
                    ("permits on 11/3", "2026-11-03"), ("what about 2026-05-03", "2026-05-03"),
                    ("Saturday, October 24, 2026", "2026-10-24")]:
        w = resolve_window(q, now)
        assert w and w.start.date().isoformat() == want, q
    w = resolve_window("closures the weekend of November 21?", now)
    assert w.start.date().isoformat() == "2026-11-21" and (w.end - w.start).days == 2
    assert resolve_window("on February 30", now) is None


OCT_14 = datetime(2026, 10, 14, 12, tzinfo=LOCAL_TZ)  # a Wednesday


def _span(q):
    w = resolve_window(q, OCT_14)
    return w and (w.start.date().isoformat(), (w.end.date()).isoformat())


def test_weeks_months_and_years():
    # Before these rules, "2nd week of November" fell through to the next 7 days without a word.
    assert _span("What's happening in Eakins Oval in the 2nd week of November?") == ("2026-11-08", "2026-11-15")
    assert _span("last week of February 2027") == ("2027-02-22", "2027-03-01")
    assert _span("the week of Nov 11") == ("2026-11-09", "2026-11-16")          # Monday to Sunday
    assert _span("anything in early December?") == ("2026-12-01", "2026-12-11")
    assert _span("November closures at Eakins Oval") == ("2026-11-01", "2026-12-01")
    assert _span("in March 2025") == ("2025-03-01", "2025-04-01")
    assert _span("next month") == ("2026-11-01", "2026-12-01")
    assert _span("next week") == ("2026-10-19", "2026-10-26")
    assert _span("in 2 weeks") == ("2026-10-28", "2026-11-04")
    assert _span("past 30 days") == ("2026-09-14", "2026-10-15")
    assert _span("in 2000") == ("2000-01-01", "2001-01-01")


def test_month_without_year_follows_tense():
    assert _span("closures in March") == ("2027-03-01", "2027-04-01")         # next March
    assert _span("what happened in March") == ("2026-03-01", "2026-04-01")    # last March
    assert _span("last November") == ("2025-11-01", "2025-12-01")


def test_words_that_are_not_dates():
    assert _span("may I park on 1500 Market St tomorrow") == ("2026-10-15", "2026-10-16")
    assert _span("the march on Broad St Saturday") == ("2026-10-17", "2026-10-18")
    assert _span("what is around 1500 Market St") is None


def test_window_kind_and_ancient_dates():
    from datetime import date
    from penn.timeutil import window_kind
    start = date(2016, 1, 1)
    for q, kind in [("year 1 A.D.", "before_records"), ("500 BC", "before_records"), ("in 2000", "before_records"),
                    ("in 2024", "past"), ("this week", "current"), ("2nd week of November", "future")]:
        w = resolve_window(q, OCT_14)
        assert w and window_kind(w, OCT_14, start) == kind, q
    assert "1 AD" in resolve_window("what about year 1 A.D.", OCT_14).label
