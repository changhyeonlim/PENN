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
