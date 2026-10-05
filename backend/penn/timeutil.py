"""Date and time handling.

Two rules matter most:

1. Lane Closure dates are date-only values stored as UTC midnight. Read them as
   calendar dates in UTC. Converting to Philadelphia time first moves every date
   back one day (midnight UTC is 8 PM the previous evening in Eastern time).
2. Relative phrases ("tomorrow", "Saturday morning") are resolved on the server
   from the request time in America/New_York, and the resolved window is echoed
   back to the user.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from .config import LOCAL_TZ

OPEN_ENDED_AFTER_DAYS = 730  # permits longer than two years are treated as placeholders


def esri_date_to_calendar_date(epoch_ms: int | None) -> date | None:
    """Convert an ArcGIS date-only value (epoch ms at UTC midnight) to its calendar date."""
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).date()


def is_open_ended(start: date | None, end: date | None) -> bool:
    if start is None or end is None:
        return end is None
    return (end - start).days > OPEN_ENDED_AFTER_DAYS


@dataclass(frozen=True)
class Window:
    start: datetime  # aware, local
    end: datetime    # aware, local, exclusive
    label: str

    def as_utc(self) -> tuple[datetime, datetime]:
        return self.start.astimezone(timezone.utc), self.end.astimezone(timezone.utc)

    def dates(self) -> list[date]:
        d, out = self.start.date(), []
        while datetime.combine(d, time.min, LOCAL_TZ) < self.end:
            out.append(d)
            d += timedelta(days=1)
        return out


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PARTS = {
    "morning": (time(6), time(12)),
    "afternoon": (time(12), time(17)),
    "evening": (time(17), time(22)),
    "night": (time(20), time(23, 59)),
}


def _day_window(d: date, part: str | None) -> tuple[datetime, datetime]:
    if part:
        a, b = PARTS[part]
        return datetime.combine(d, a, LOCAL_TZ), datetime.combine(d, b, LOCAL_TZ)
    return datetime.combine(d, time.min, LOCAL_TZ), datetime.combine(d + timedelta(days=1), time.min, LOCAL_TZ)


def resolve_window(text: str, now: datetime | None = None) -> Window | None:
    """Resolve a relative time phrase to a local window. Returns None if nothing matched.

    Handles: today, tonight, tomorrow, a weekday name (next occurrence, today included),
    this weekend, next N days, optionally followed by morning/afternoon/evening/night.
    """
    now = (now or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    t = text.lower()
    part = next((p for p in PARTS if p in t), None)
    if "tonight" in t:
        part = "night"

    if m := re.search(r"next (\d{1,2}) days", t):
        n = int(m.group(1))
        start = datetime.combine(now.date(), time.min, LOCAL_TZ)
        return Window(start, start + timedelta(days=n), f"the next {n} days")

    if "this weekend" in t or t.strip() == "weekend":
        days_to_sat = (5 - now.weekday()) % 7
        sat = now.date() + timedelta(days=days_to_sat)
        start = datetime.combine(sat, time.min, LOCAL_TZ)
        return Window(start, start + timedelta(days=2), f"the weekend of {sat:%b %-d}")

    if "tomorrow" in t:
        d = now.date() + timedelta(days=1)
    elif "today" in t or "tonight" in t:
        d = now.date()
    else:
        d = None
        for i, name in enumerate(WEEKDAYS):
            if name in t:
                d = now.date() + timedelta(days=(i - now.weekday()) % 7)
                break
    if d is None:
        return None
    s, e = _day_window(d, part)
    label = f"{d:%A %b %-d}" + (f" {part}" if part else "")
    return Window(s, e, label)
