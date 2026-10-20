"""p3-12: ticketed events in Philadelphia from the Ticketmaster Discovery API, as listing rows.

    GET https://app.ticketmaster.com/discovery/v2/events.json?city=Philadelphia&stateCode=PA
        &startDateTime=...&endDateTime=...&size=200&page=N&sort=date,asc

Covers big ticketed events (concerts, games, theatre) that never need a road-closure permit. Rows are
listings like visitphilly's: a point at the venue, no closure implied.

Terms (developer.ticketmaster.com/support/terms-of-use, checked Oct 17, 2026):
- Event Content may be stored only "for reasonable periods in order to provide the service": each run
  fetches the next WINDOW_DAYS and the job deletes every ticketmaster row the run did not return, so
  past, removed and cancelled events leave the database the next morning.
- Only what the map needs is kept: name, dates, venue, link, segment. Not the "info" and "pleaseNote"
  text, and nothing goes to raw_records.
- One run is a handful of calls (about 4 pages for 60 days, against 5,000 a day).
- source_url is the event's Ticketmaster page; organization names Ticketmaster as the lister.

The API pages at most 1,000 results per search (size x page < 1000), so a window with more is split
in half until each part fits.

Venue pins are not trusted as given: Ticketmaster put Stateside Live (1100 Pattison Ave) 1.5 km away,
and keeps two records for Walnut Street Theatre, one 420 m north (Oct 17: 13 of about 30 venues more
than 150 m off). correct_venues looks each venue's street address up in the City's address search (AIS)
and uses the City's point for every address AIS finds, so two records for one venue land on the same
spot; moves over MAX_PIN_ERROR_M are reported. AIS rate-limits bursts (HTTP 429), so lookups are paced
and retried. A performance listed twice (same title, time and place, once per venue record) is then
kept once.
"""
from __future__ import annotations

import re
import time as clock
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

import httpx

from ..config import LOCAL_TZ, settings
from ..geo.h3util import point_cell
from ..models import NormalizedEvent
from ..overlaps import distinctive, metres
from .visitphilly import in_city

SOURCE = "ticketmaster"
URL = "https://app.ticketmaster.com/discovery/v2/events.json"
USER_AGENT = "PENN-hackathon/0.1 (Philadelphia street-closure map; daily batch)"
WINDOW_DAYS = 60
PAGE_SIZE = 200
DEEP_LIMIT = 1000           # the API refuses page * size >= 1000
SKIP_STATUS = {"cancelled"}  # postponed and rescheduled stay, with the status shown
# Products sold as their own "events": suites, parking, VIP packages and game-day extras that need a
# ticket to the real event ("Post Game Slap Shot - MUST HAVE TICKET TO GAME"). They are not events.
AIS_URL = "https://api.phila.gov/ais/v1/search/{q}"
MAX_PIN_ERROR_M = 150
ADD_ON = re.compile(r"must have (?:a )?(?:game )?ticket|^suites?\b|\bparking\b|^vip\b|\bpackage\b|\bupgrade\b", re.I)


class NoKey(RuntimeError):
    pass


@dataclass
class FetchReport:
    calls: int = 0
    events: int = 0
    skipped_status: list[str] = field(default_factory=list)
    add_ons: list[str] = field(default_factory=list)
    outside: list[str] = field(default_factory=list)
    no_location: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)       # "<venue>: <metres> m" for pins replaced from AIS
    duplicates: int = 0


def _utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _page(client: httpx.Client, start: datetime, end: datetime, page: int) -> dict:
    r = client.get(URL, params={
        "apikey": settings.ticketmaster_api_key, "city": "Philadelphia", "stateCode": "PA",
        "startDateTime": _utc(start), "endDateTime": _utc(end), "size": PAGE_SIZE, "page": page,
        "sort": "date,asc",
    })
    r.raise_for_status()
    return r.json()


def fetch(start: datetime, days: int = WINDOW_DAYS, client: httpx.Client | None = None,
          rep: FetchReport | None = None) -> list[dict]:
    """Every event in [start, start + days), splitting the window while a part exceeds DEEP_LIMIT."""
    if not settings.ticketmaster_api_key:
        raise NoKey("TICKETMASTER_API_KEY is not set in .env")
    client = client or httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    rep = rep if rep is not None else FetchReport()
    out: dict[str, dict] = {}

    def window(a: datetime, b: datetime) -> None:
        first = _page(client, a, b, 0)
        rep.calls += 1
        total = first.get("page", {}).get("totalElements", 0)
        if total >= DEEP_LIMIT and b - a > timedelta(hours=12):
            mid = a + (b - a) / 2
            window(a, mid)
            window(mid, b)
            return
        pages = [first]
        for n in range(1, first.get("page", {}).get("totalPages", 1)):
            if (n + 1) * PAGE_SIZE > DEEP_LIMIT:
                break
            pages.append(_page(client, a, b, n))
            rep.calls += 1
        for p in pages:
            for e in p.get("_embedded", {}).get("events", []):
                out[e["id"]] = e

    window(start, start + timedelta(days=days))
    return list(out.values())


def _venue(e: dict) -> dict:
    return (e.get("_embedded", {}).get("venues") or [{}])[0]


def where(e: dict) -> str:
    """'Xfinity Mobile Arena, 3601 S. Broad St., Philadelphia, PA', as visitphilly writes places."""
    v = _venue(e)
    parts = [v.get("name"), (v.get("address") or {}).get("line1"), (v.get("city") or {}).get("name"),
             (v.get("state") or {}).get("stateCode")]
    return ", ".join(p.strip() for p in parts if p and p.strip())


def segment(e: dict) -> str:
    c = next((c for c in e.get("classifications") or [] if c.get("primary")), (e.get("classifications") or [{}])[0])
    name = ((c or {}).get("segment") or {}).get("name") or ""
    return "" if name in ("Undefined", "Miscellaneous") else name


def to_event(e: dict) -> NormalizedEvent | None:
    """None when the event has no venue point inside the city."""
    v = _venue(e)
    loc = v.get("location") or {}
    try:
        lon, lat = float(loc["longitude"]), float(loc["latitude"])
    except (KeyError, TypeError, ValueError):
        return None
    place = where(e)
    if not in_city(lon, lat, place):
        return None
    d = e.get("dates", {})
    st = d.get("start", {})
    status = (d.get("status") or {}).get("code", "")
    start_date = date.fromisoformat(st["localDate"]) if st.get("localDate") else None
    start_ts = None
    if st.get("dateTime") and not st.get("timeTBA") and not st.get("noSpecificTime"):
        start_ts = datetime.fromisoformat(st["dateTime"].replace("Z", "+00:00"))
    elif start_date and st.get("localTime") and not st.get("timeTBA"):
        start_ts = datetime.combine(start_date, time.fromisoformat(st["localTime"]), LOCAL_TZ)
    end = d.get("end", {})
    end_date = date.fromisoformat(end["localDate"]) if end.get("localDate") else start_date
    end_ts = datetime.fromisoformat(end["dateTime"].replace("Z", "+00:00")) if end.get("dateTime") else None
    seg = segment(e)
    desc = "; ".join(x for x in (place, seg, status.capitalize() if status in ("postponed", "rescheduled") else "") if x)
    cell = point_cell(lon, lat)
    return NormalizedEvent(
        source=SOURCE,
        source_record_id=e["id"],
        event_type="event",
        title=re.sub(r"\s+", " ", e.get("name", "")).strip(),
        description=desc,
        source_url=e.get("url") or "",
        source_authority="curated",
        claim_certainty="scheduled",
        geometry_wkt=f"POINT ({lon:.6f} {lat:.6f})",
        centroid_lat=lat,
        centroid_lon=lon,
        h3_r9=cell,
        h3_cells=[cell],
        organization="Ticketmaster",
        occupancy="venue",
        start_date=start_date,
        end_date=end_date,
        start_ts=start_ts,
        end_ts=end_ts,
        status=status or "listed",
    )


def ais_point(address: str, client: httpx.Client, tries: int = 4, wait: float = 1.5) -> tuple[float, float] | None:
    """(lon, lat) of a Philadelphia street address from the City's AIS, or None. A 429 (rate limit) is
    retried after Retry-After, or wait x attempt seconds."""
    params = {"gatekeeperKey": settings.ais_gatekeeper_key} if settings.ais_gatekeeper_key else {}
    feats: list = []
    for attempt in range(1, tries + 1):
        try:
            r = client.get(AIS_URL.format(q=address), params=params)
        except httpx.HTTPError:
            return None
        if r.status_code == 429 and attempt < tries:
            retry = getattr(r, "headers", {}).get("Retry-After", "")
            clock.sleep(float(retry) if retry.isdigit() else wait * attempt)
            continue
        try:
            feats = r.json().get("features", []) if r.status_code == 200 else []
        except ValueError:
            return None
        break
    if not feats:
        return None
    lon, lat = feats[0]["geometry"]["coordinates"]
    return float(lon), float(lat)


def correct_venues(raw: list[dict], client: httpx.Client | None = None, rep: FetchReport | None = None,
                   pace: float = 0.3) -> None:
    """Put each Philadelphia venue on the City's point for its street address. One paced AIS lookup per
    address per run; an address AIS cannot find keeps Ticketmaster's pin. Moves over MAX_PIN_ERROR_M go
    into rep.moved."""
    client = client or httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT})
    rep = rep if rep is not None else FetchReport()
    found: dict[str, tuple[float, float] | None] = {}
    for e in raw:
        v = _venue(e)
        line1 = ((v.get("address") or {}).get("line1") or "").strip()
        loc = v.get("location") or {}
        if not line1 or ((v.get("city") or {}).get("name") or "").lower() != "philadelphia" or not loc.get("latitude"):
            continue
        key = line1.lower()
        if key not in found:
            if found and pace:
                clock.sleep(pace)
            found[key] = ais_point(line1, client)
        pt = found[key]
        if pt is None:
            continue
        lat, lon = float(loc["latitude"]), float(loc["longitude"])
        off = metres(lat, lon, pt[1], pt[0])
        v["location"] = {"latitude": str(pt[1]), "longitude": str(pt[0])}
        note = f"{v.get('name', line1)}: {round(off)} m"
        if off > MAX_PIN_ERROR_M and note not in rep.moved:
            rep.moved.append(note)


def to_events(raw: list[dict], rep: FetchReport | None = None) -> list[NormalizedEvent]:
    rep = rep if rep is not None else FetchReport()
    out = []
    seen: dict[tuple, list[tuple[NormalizedEvent, set[str]]]] = {}
    alias: dict[tuple, str] = {}          # ((lat, lon), second record's title) -> the first record's title
    for e in raw:
        name = e.get("name", "")
        if (e.get("dates", {}).get("status") or {}).get("code") in SKIP_STATUS:
            rep.skipped_status.append(name)
            continue
        if e.get("test"):
            continue
        if ADD_ON.search(name):
            rep.add_ons.append(name)
            continue
        ev = to_event(e)
        if ev is None:
            (rep.outside if (_venue(e).get("location") or {}).get("latitude") else rep.no_location).append(
                f"{name}: {where(e) or 'no venue'}")
            continue
        # The same performance under a second venue record, often titled differently ("The Addams Family
        # (Touring)" and "Walnut Street Theatre's THE ADDAMS FAMILY: The Musical"): same start and place,
        # and at least half of the shorter title's distinctive words shared.
        key = (ev.start_ts or ev.start_date, round(ev.centroid_lat, 4), round(ev.centroid_lon, 4))
        words = distinctive(ev.title, ev.description)
        twin = next((k for k, w in seen.get(key, []) if len(words & w) >= max(1, -(-min(len(words), len(w)) // 2))), None)
        if twin is not None:
            rep.duplicates += 1
            alias[(key[1:], ev.title)] = twin.title      # this record's name for the show, at this place
            continue
        seen.setdefault(key, []).append((ev, words))
        out.append(ev)
    # Dates only the second venue record lists take the first record's title, so the map groups the run
    # as one show (it groups by title and place).
    for ev in out:
        ev.title = alias.get(((round(ev.centroid_lat, 4), round(ev.centroid_lon, 4)), ev.title), ev.title)
    rep.events = len(out)
    return out
