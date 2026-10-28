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


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                       "nov", "dec"], start=1)}
DATE_WORDS = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
                        r"(?:,?\s+(\d{4}))?\b")
DATE_NUM = re.compile(r"\b(?:(\d{4})-(\d{1,2})-(\d{1,2})|(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?)\b")


def _explicit_date(t: str, now: datetime) -> date | None:
    """ "November 22", "Nov. 22nd, 2026", "11/22", "2026-11-22". No year = this year."""
    try:
        if m := DATE_WORDS.search(t):
            mon = MONTHS[m.group(1)[:3]]
            return date(int(m.group(3) or now.year), mon, int(m.group(2)))
        if m := DATE_NUM.search(t):
            if m.group(1):
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            y = m.group(6)
            year = (2000 + int(y) if len(y) == 2 else int(y)) if y else now.year
            return date(year, int(m.group(4)), int(m.group(5)))
    except ValueError:                                       # "February 30": not a date
        return None
    return None


MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
               "october", "november", "december"]
MONTH_WORD = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
# "may" and "march" are also ordinary words ("may I park", "the march on Broad St"), so a bare one
# only counts as a month after one of these words or before a year.
MONTH_CONTEXT = r"(?:in|of|during|for|this|next|last|until|through|since|around|before|after)"
MONTH_ONLY = re.compile(rf"\b(?:(?P<ctx>{MONTH_CONTEXT})\s+(?:the\s+)?)?(?:(?P<part>early|mid|late)[\s-]+)?"
                        rf"{MONTH_WORD}\.?(?:,?\s+(?P<year>\d{{4}}))?\b(?!\s*\d)")
ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
            "fifth": 5, "5th": 5, "last": -1, "final": -1}
WEEK_OF_MONTH = re.compile(rf"\b({'|'.join(ORDINALS)})\s+week\s+(?:of|in)\s+{MONTH_WORD}\.?(?:,?\s+(\d{{4}}))?\b")
PAST_TENSE = re.compile(r"\b(was|were|did|happened|had|ago)\b")
YEAR_ERA = re.compile(r"\b(?:year\s+)?(\d{1,4})\s*(a\.?d\.?|b\.?c\.?e?\.?|c\.?e\.?)(?=\W|$)|\ba\.?d\.?\s*(\d{1,4})\b")
YEAR_WORD = re.compile(r"\byear\s+(\d{1,4})\b(?!\s*(?:days?|weeks?|months?|years?))")
# A bare 4-digit number is usually an address ("1500 Market St"), so a year needs a word before it.
YEAR_4 = re.compile(r"\b(?:in|during|for|of|since|from)\s+(1\d{3}|2\d{3})\b(?![/\-\d])")
SPAN = re.compile(r"\b(next|coming|past|last)\s+(\d{1,2}|two|three|four|five|six)\s+(days?|weeks?|months?)\b")
IN_SPAN = re.compile(r"\bin\s+(\d{1,2}|a|one|two|three|four|five|six)\s+(days?|weeks?)\b")
SMALL = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _month_num(word: str) -> int:
    return MONTHS[word[:3]]


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _next_month(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def _month_year(month: int, year: str | None, now: datetime, past: bool) -> int:
    """No year given: the month's next occurrence (this month included), or its last one when
    the question is about the past ("last November", "what happened in March")."""
    if year:
        return int(year)
    if past:
        return now.year if month <= now.month else now.year - 1
    return now.year if month >= now.month else now.year + 1


def _range(a: date, b: date, label: str) -> Window:
    """[a, b] inclusive calendar dates as a local window."""
    return Window(datetime.combine(a, time.min, LOCAL_TZ), datetime.combine(b + timedelta(days=1), time.min, LOCAL_TZ),
                  label)


def _span_label(a: date, b: date) -> str:
    if a.year != b.year:
        return f"{a:%B} {a.day}, {a.year} – {b:%B} {b.day}, {b.year}"
    if a.month != b.month:
        return f"{a:%B} {a.day} – {b:%B} {b.day}, {b.year}"
    return f"{a:%B} {a.day} – {b.day}, {b.year}"


def _year_window(y: int, era: str = "") -> Window:
    """A whole year. BC years and year 1 get a placeholder window at the start of the calendar:
    PENN's records start in 2016, so only the label matters for them."""
    if era.startswith("b") or y < 2:
        start = datetime(1, 1, 2, tzinfo=LOCAL_TZ)
        return Window(start, start + timedelta(days=364), f"the year {y} {'BC' if era.startswith('b') else 'AD'}")
    return _range(date(y, 1, 1), date(y, 12, 31), f"the year {y}")


def _calendar_phrase(t: str, now: datetime) -> Window | None:
    """Weeks, months and years: "2nd week of November", "the week of Nov 9", "early December",
    "in March 2025", "next month", "last year", "in 2000", "1 AD"."""
    past = bool(PAST_TENSE.search(t)) or bool(re.search(r"\blast\s+" + MONTH_WORD, t))
    today = now.date()

    if m := WEEK_OF_MONTH.search(t):
        n, mon = ORDINALS[m.group(1)], _month_num(m.group(2))
        y = _month_year(mon, m.group(3), now, past)
        last_day = _next_month(_month_start(y, mon)) - timedelta(days=1)
        a = last_day - timedelta(days=6) if n == -1 else date(y, mon, 1) + timedelta(days=7 * (n - 1))
        if a.month != mon:                                    # "5th week of February"
            return None
        b = min(a + timedelta(days=6), last_day)
        return _range(a, b, f"{_span_label(a, b)} (the {m.group(1)} week of {MONTH_NAMES[mon - 1].title()})")

    if re.search(r"\bweek of\b", t) and (d := _explicit_date(t, now)):
        a = d - timedelta(days=d.weekday())
        return _range(a, a + timedelta(days=6), f"the week of {_span_label(a, a + timedelta(days=6))}")

    if _explicit_date(t, now):                                 # a single day: resolve_window handles it
        return None

    if m := YEAR_ERA.search(t):
        y = int(m.group(1) or m.group(3))
        return _year_window(y, (m.group(2) or "a").replace(".", ""))
    if m := YEAR_WORD.search(t):
        return _year_window(int(m.group(1)))

    for m in MONTH_ONLY.finditer(t):
        word = m.group(3)
        if word in ("may", "mar", "march") and not (m.group("ctx") or m.group("part") or m.group("year")):
            continue
        mon = _month_num(word)
        y = _month_year(mon, m.group("year"), now, past or (m.group("ctx") == "last"))
        a, last_day = date(y, mon, 1), _next_month(date(y, mon, 1)) - timedelta(days=1)
        part = m.group("part")
        if part:
            a, b = {"early": (a, a.replace(day=10)), "mid": (a.replace(day=11), a.replace(day=20)),
                    "late": (a.replace(day=21), last_day)}[part]
            return _range(a, b, f"{part} {MONTH_NAMES[mon - 1].title()} {y} ({_span_label(a, b)})")
        return _range(a, last_day, f"{MONTH_NAMES[mon - 1].title()} {y}")

    if m := YEAR_4.search(t):
        return _year_window(int(m.group(1)))

    if m := SPAN.search(t):
        n = int(SMALL.get(m.group(2), m.group(2) if m.group(2).isdigit() else 0))
        unit = m.group(3).rstrip("s")
        days = {"day": n, "week": 7 * n}.get(unit, 30 * n)
        if m.group(1) in ("past", "last"):
            return _range(today - timedelta(days=days), today, f"the past {n} {unit}{'s' if n > 1 else ''}")
        if unit == "day":
            return None                                         # "next N days" keeps its own rule below
        return _range(today, today + timedelta(days=days - 1), f"the next {n} {unit}s")

    if m := IN_SPAN.search(t):                                  # "in 2 weeks": that week; "in 3 days": that day
        n = int(SMALL.get(m.group(1), m.group(1) if m.group(1).isdigit() else 0))
        if m.group(2).startswith("week"):
            a = today + timedelta(days=7 * n)
            return _range(a, a + timedelta(days=6), f"the week starting {a:%B} {a.day}, {a.year}")
        d = today + timedelta(days=n)
        return _range(d, d, f"{d:%A, %B} {d.day}, {d.year}")

    monday = today - timedelta(days=today.weekday())
    if re.search(r"\bnext week\b", t):
        a = monday + timedelta(days=7)
        return _range(a, a + timedelta(days=6), f"next week ({_span_label(a, a + timedelta(days=6))})")
    if re.search(r"\bthis week\b", t):
        return _range(today, monday + timedelta(days=6), f"this week ({_span_label(today, monday + timedelta(days=6))})")
    if re.search(r"\blast week\b", t):
        a = monday - timedelta(days=7)
        return _range(a, a + timedelta(days=6), f"last week ({_span_label(a, a + timedelta(days=6))})")

    this_month = today.replace(day=1)
    for word, first in (("next", _next_month(this_month)), ("this", this_month),
                        ("last", (this_month - timedelta(days=1)).replace(day=1))):
        if re.search(rf"\b{word} month\b", t):
            last_day = _next_month(first) - timedelta(days=1)
            return _range(first, last_day, f"{first:%B %Y}")
    for word, y in (("next", today.year + 1), ("this", today.year), ("last", today.year - 1)):
        if re.search(rf"\b{word} year\b", t):
            return _year_window(y)
    return None


def window_kind(w: Window, now: datetime, records_start: date) -> str:
    """Where a window sits against PENN's records, which decides the answer format:

    before_records  ends before PENN's first record: a fixed answer, no model call
    past            ended before now: answer from what the records show
    current         includes now: the default
    future          starts after now: permits may not be filed yet, so fall back to past years here
    """
    if w.end <= datetime.combine(records_start, time.min, LOCAL_TZ):
        return "before_records"
    if w.end <= now:
        return "past"
    if w.start > now:
        return "future"
    return "current"


def before_records_answer(label: str, records_start: date) -> str:
    return (f"PENN has no records for {label}. Its records start in {records_start.year}: City street "
            f"permits, City-announced event closures and listed events for Philadelphia. Ask about "
            f"{records_start.year} or later, or about upcoming dates.")


TIME_HINT = re.compile(rf"\b{MONTH_WORD}\b|\b(?:19|20)\d\d\b|\byear\b|\bweek\b|\bmonth\b|\b(?:a\.?d|b\.?c)\b")


def mentions_time(text: str) -> bool:
    """Whether the question names a time PENN might have failed to read (a month, a year, a week).
    Used to warn instead of silently answering for the default window."""
    t = text.lower()
    return any(m.group(0) not in ("may", "mar", "march") for m in TIME_HINT.finditer(t))


def now_local() -> datetime:
    """The current local time, or PENN_NOW when a demo pins the clock."""
    from .config import settings
    if settings.pinned_now:
        return datetime.fromisoformat(settings.pinned_now).astimezone(LOCAL_TZ)
    return datetime.now(LOCAL_TZ)


def resolve_window(text: str, now: datetime | None = None) -> Window | None:
    """Resolve a time phrase to a local window. Returns None if nothing matched.

    Handles: an explicit date ("November 22", "11/22", "2026-11-22"; "the weekend of Nov 21"
    covers that Saturday and Sunday), weeks, months and years ("2nd week of November", "the week of
    Nov 9", "early December", "in March 2025", "next month", "in 2000", "1 AD"), today, tonight,
    tomorrow, a weekday name (next occurrence, today included), this weekend, next N days,
    optionally followed by morning/afternoon/evening/night.
    An explicit date wins over a weekday name ("Saturday, November 22" is November 22).
    A month or week with no year is its next occurrence, or its last one when the question is in
    the past tense ("what happened in March").
    """
    now = (now or now_local()).astimezone(LOCAL_TZ)
    t = text.lower()
    part = next((p for p in PARTS if p in t), None)
    if "tonight" in t:
        part = "night"

    if w := _calendar_phrase(t, now):
        return w

    if d := _explicit_date(t, now):
        if "weekend of" in t:
            sat = d - timedelta(days=(d.weekday() - 5) % 7) if d.weekday() >= 5 else d + timedelta(days=5 - d.weekday())
            start = datetime.combine(sat, time.min, LOCAL_TZ)
            return Window(start, start + timedelta(days=2), f"the weekend of {sat:%b} {sat.day}, {sat.year}")
        s, e = _day_window(d, part)
        return Window(s, e, f"{d:%A, %B} {d.day}, {d.year}" + (f" {part}" if part else ""))

    if m := re.search(r"next (\d{1,2}) days", t):
        n = int(m.group(1))
        start = datetime.combine(now.date(), time.min, LOCAL_TZ)
        return Window(start, start + timedelta(days=n), f"the next {n} days")

    if "this weekend" in t or t.strip() == "weekend":
        days_to_sat = (5 - now.weekday()) % 7
        sat = now.date() + timedelta(days=days_to_sat)
        start = datetime.combine(sat, time.min, LOCAL_TZ)
        return Window(start, start + timedelta(days=2), f"the weekend of {sat:%b} {sat.day}")

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
    label = f"{d:%A %b} {d.day}" + (f" {part}" if part else "")
    return Window(s, e, label)
