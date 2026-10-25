"""Tavily web search, the fallback for what PENN has no record of (p5-09).

find_event (penn/agent/context.py) calls search() when an event is not in PENN's catalog, or has no
City notice for its coming edition. search_web calls search_open() for an open question PENN's
records do not answer ("any food festival in Philadelphia?"); the loop sends a "no record" answer
back to it once, so PENN looks on the web before saying it knows nothing.

- What leaves PENN: the event's name and a year ("Rocky Run Philadelphia 2026"), or the model's short
  query (what and where, at most QUERY_CHARS), never the user's message. Tavily may keep and use what
  it receives (terms 6.5, 9.2), so the fine print says so.
- phila.gov and the event's own sites (event_series.csv `sites`) are preferred, not required
  (include_domains_mode "prefer"), so an organizer page still surfaces.
- Each result is a citable record, event_id "web:<8 hex of sha1(url)>". The page text stays server
  side for the fact check (citations.web_facts); the model reads the excerpt.
- Searches are cached in data/web_cache for CACHE_HOURS. With the clock pinned (demo mode, PENN_NOW)
  only the cache is read, so a recording gives the same answer every take and makes no web calls.
- A failure (no key, 429, plan limit, network) returns no results and a note; the turn goes on.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time as clock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..config import settings

URL = "https://api.tavily.com/search"
CACHE = Path(__file__).resolve().parents[3] / "data" / "web_cache"
CACHE_HOURS = 24
MAX_RESULTS = 5
PREFERRED = ["phila.gov"]
OPEN_PREFERRED = ["visitphilly.com"]   # the city's tourism site lists festivals and their usual months
QUERY_CHARS = 120                      # an open query is a few words; never a pasted message
TEXT_CHARS = 20_000          # page text kept for the fact check
EXCERPT_CHARS = 900          # what the model reads per result


def web_id(url: str) -> str:
    return "web:" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]


def _key(query: str, domains: list[str]) -> str:
    return hashlib.sha1(json.dumps([query, sorted(domains)]).encode("utf-8")).hexdigest()[:16]


def body(name: str, year: int, sites: list[str]) -> dict:
    return {
        "query": f"{name} {year}" if "philadelphia" in name.lower() else f"{name} Philadelphia {year}",
        "search_depth": "basic",
        "max_results": MAX_RESULTS,
        "include_raw_content": "text",
        "include_domains": PREFERRED + [s for s in sites if s and s not in PREFERRED],
        "include_domains_mode": "prefer",
        "country": "united states",
        "start_date": f"{year - 1}-01-01",
    }


def open_body(query: str) -> dict:
    """An open question ("East Passyunk food festival"): the model's short query, in Philadelphia, with
    no date filter, since "does it ever happen" is answered by an organizer's evergreen page."""
    q = re.sub(r"\s+", " ", query).strip()[:QUERY_CHARS]
    return {
        "query": q if re.search(r"philadelphia|philly", q, re.I) else f"{q} Philadelphia",
        "search_depth": "basic",
        "max_results": MAX_RESULTS,
        "include_raw_content": "text",
        "include_domains": PREFERRED + OPEN_PREFERRED,
        "include_domains_mode": "prefer",
        "country": "united states",
    }


def _records(results: list[dict]) -> list[dict]:
    out = []
    for r in results:
        url = r.get("url") or ""
        if not url:
            continue
        out.append({
            "event_id": web_id(url),
            "title": (r.get("title") or "").strip(),
            "url": url,
            "site": urlparse(url).netloc.removeprefix("www."),
            "published": r.get("published_date"),
            "excerpt": (r.get("content") or "").strip()[:EXCERPT_CHARS],
            "text": (r.get("raw_content") or r.get("content") or "")[:TEXT_CHARS],
        })
    return out


def search(name: str, year: int, sites: list[str] | None = None, client: httpx.Client | None = None,
           cache_dir: Path = CACHE, now: datetime | None = None) -> dict:
    """{"results": [record, ...], "query": ..., "cached": bool} or {"results": [], "note": why}."""
    return _run(body(name, year, sites or []), client, cache_dir, now)


def search_open(query: str, client: httpx.Client | None = None, cache_dir: Path = CACHE,
                now: datetime | None = None) -> dict:
    """An open question PENN's records do not answer; same result shape as search()."""
    return _run(open_body(query), client, cache_dir, now)


_searches: dict[str, int] = {}     # UTC date -> searches sent to Tavily that day
_searches_lock = threading.Lock()


def _take_search(now: datetime) -> bool:
    """One network search from today's allowance (settings.web_searches_per_day; 0 = no limit)."""
    limit = getattr(settings, "web_searches_per_day", 0)
    if not limit:
        return True
    day = now.strftime("%Y-%m-%d")
    with _searches_lock:
        for d in [d for d in _searches if d != day]:
            del _searches[d]
        if _searches.get(day, 0) >= limit:
            return False
        _searches[day] = _searches.get(day, 0) + 1
        return True


def _run(req: dict, client: httpx.Client | None, cache_dir: Path, now: datetime | None) -> dict:
    path = cache_dir / f"{_key(req['query'], req['include_domains'])}.json"
    now = now or datetime.now(timezone.utc)
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        fresh = now - datetime.fromisoformat(saved["at"]) < timedelta(hours=CACHE_HOURS)
        if fresh or settings.pinned_now:
            return {"results": saved["results"], "query": req["query"], "cached": True}
    if settings.pinned_now:
        return {"results": [], "query": req["query"], "note": "demo mode: no saved web search for this event"}
    if not settings.tavily_api_key:
        return {"results": [], "query": req["query"], "note": "web search is not set up (no Tavily key)"}
    if not _take_search(now):
        return {"results": [], "query": req["query"], "note": "the web-search limit for today is reached"}
    client = client or httpx.Client(timeout=30)
    for attempt in (1, 2):
        try:
            r = client.post(URL, json=req, headers={"Authorization": f"Bearer {settings.tavily_api_key}"})
        except httpx.HTTPError as exc:
            return {"results": [], "query": req["query"], "note": f"web search failed ({type(exc).__name__})"}
        if r.status_code == 429 and attempt == 1:
            retry = r.headers.get("Retry-After", "")
            clock.sleep(min(float(retry), 10) if retry.replace(".", "", 1).isdigit() else 2)
            continue
        break
    if r.status_code != 200:
        why = {401: "the Tavily key was refused", 429: "Tavily is rate-limiting", 432: "the Tavily plan limit is reached",
               433: "the Tavily pay-as-you-go limit is reached"}.get(r.status_code, f"Tavily answered {r.status_code}")
        return {"results": [], "query": req["query"], "note": f"web search unavailable: {why}"}
    results = _records(r.json().get("results") or [])
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"at": now.isoformat(), "query": req["query"], "results": results}), encoding="utf-8")
    return {"results": results, "query": req["query"], "cached": False}
