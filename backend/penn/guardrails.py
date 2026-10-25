"""Guardrails for the public demo: what one visitor, or a fleet of bots, can make PENN spend.

Off unless PENN_PUBLIC=1, which the deployed endpoint sets (deploy/endpoint/start.sh); local runs, tests and the
eval are unaffected. Every count is per API process (the endpoint runs two, live and demo) and starts over when
the process restarts. Layers, cheapest first:

1. Size: a question is at most MAX_QUESTION characters; history, earlier answers and the context the browser
   sends back are trimmed, so padding a request can't make the model read more.
2. Per visitor: requests per minute on every route; fewer on /locate, which can reach the City's address
   service; asks per minute and per day on /agent.
3. Answer cache: the same question (same place, context and history) gets the stored answer. With the clock
   pinned (the demo view) the data can't change, so answers are kept, on disk too; live ones for CACHE_MINUTES.
4. Fresh answers, per pool: judges (requests carrying PENN_JUDGE_CODE) and visitors (everyone else, bots
   included) have separate per-minute, per-hour and per-day ceilings and separate concurrency slots, so nothing
   visitors do can use up a judge's answers. Rotating addresses gets past (2), not past this; the hourly slice
   keeps a flood from using up a whole day. Wrong codes are limited per visitor, so the code can't be guessed.
5. Per question: token and time ceilings (settings.turn_*, in agent/loop.py) and a daily cap on searches that
   reach Tavily (settings.web_searches_per_day, in web/tavily.py).

The kill switch is outside the code: revoke the deployment's Token Factory key in the Nebius console, and Ask
PENN stops spending at once while the map keeps working.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import LOCAL_TZ, settings


def _env(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


PUBLIC = os.getenv("PENN_PUBLIC", "0") == "1"
# Where the visitor's address comes from. Next.js forwards the browser's X-Forwarded-For unchanged (it adds
# nothing), so a bot can claim any address there; only entries appended by a proxy in front of us can be
# trusted. PENN_TRUSTED_PROXIES = how many such proxies append to X-Forwarded-For (the endpoint's ingress: 1);
# the visitor is the entry the outermost one added. PENN_CLIENT_IP_HEADER, if the ingress instead sets its own
# header (e.g. x-real-ip), takes precedence. Neither set: the TCP peer (fine locally).
TRUSTED_PROXIES = _env("PENN_TRUSTED_PROXIES", 0)
CLIENT_IP_HEADER = os.getenv("PENN_CLIENT_IP_HEADER", "").strip().lower()
_ADDRESS_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "x-envoy-external-address", "true-client-ip",
                    "cf-connecting-ip", "x-client-ip")

MAX_QUESTION = _env("PENN_MAX_QUESTION_CHARS", 300)
MAX_HISTORY_TURNS, MAX_HISTORY_QUESTION, MAX_HISTORY_ANSWER = 3, 300, 2000
MAX_CONTEXT_BYTES = _env("PENN_MAX_CONTEXT_BYTES", 2000)

REQUESTS_PER_MINUTE = _env("PENN_IP_REQUESTS_PER_MINUTE", 120)
LOCATE_PER_MINUTE = _env("PENN_IP_LOCATE_PER_MINUTE", 20)

# Two pools. A judge (a request carrying the access code, PENN_JUDGE_CODE) draws from an allowance no one else can
# touch; everyone else, bots included, shares the visitor pool. Each pool has asks per visitor (minute, day) and
# fresh answers for the whole pool (minute, hour, day) and its own concurrency slots. The day comes in hourly
# slices, so a flood can only use up its own hour. Cached answers are served before any of these ceilings.
JUDGE_CODE = os.getenv("PENN_JUDGE_CODE", "").strip()
POOLS = {
    "visitor": {"asks_minute": _env("PENN_VISITOR_ASKS_PER_MINUTE", 5), "asks_day": _env("PENN_VISITOR_ASKS_PER_DAY", 20),
                "fresh_minute": _env("PENN_VISITOR_FRESH_PER_MINUTE", 5),
                "fresh_hour": _env("PENN_VISITOR_FRESH_PER_HOUR", 8), "fresh_day": _env("PENN_VISITOR_FRESH_PER_DAY", 30),
                "slots": _env("PENN_VISITOR_CONCURRENT", 2)},
    "judge": {"asks_minute": _env("PENN_JUDGE_ASKS_PER_MINUTE", 10), "asks_day": _env("PENN_JUDGE_ASKS_PER_DAY", 200),
              "fresh_minute": _env("PENN_JUDGE_FRESH_PER_MINUTE", 10),
              "fresh_hour": _env("PENN_JUDGE_FRESH_PER_HOUR", 30), "fresh_day": _env("PENN_JUDGE_FRESH_PER_DAY", 100),
              "slots": _env("PENN_JUDGE_CONCURRENT", 2)},
}
BAD_CODE_PER_HOUR = 10                       # wrong judge codes per visitor per hour, so it can't be guessed

CACHE_MINUTES = _env("PENN_ANSWER_CACHE_MINUTES", 30)
CACHE_MAX = 2000
CACHE_FILE = Path(__file__).resolve().parents[2] / "data" / "answer_cache" / f"{settings.clickhouse_db}.jsonl"
USAGE_DIR = Path(__file__).resolve().parents[2] / "data" / "usage"


class Limited(Exception):
    """A request the guardrails turn away: the HTTP status, a message the page shows as is, and a short reason for
    the usage log."""

    def __init__(self, status: int, message: str, reason: str = "limited"):
        super().__init__(message)
        self.status, self.message, self.reason = status, message, reason


SLOW_DOWN = "You're asking faster than the demo allows. Wait a minute, then ask again."
DAY_IS_UP = "That's the most questions one visitor can ask the demo today. The map still works; come back tomorrow."
DEMO_FULL = ("The demo has answered all the new questions it can today. The map, and questions already asked, "
             "still work.")
HOUR_FULL = ("The demo has answered all the new questions it can this hour. Questions already asked still work; "
             "try a new one a little later.")
BUSY = "PENN is answering several questions right now. Try again in a few seconds."
TOO_LONG = f"Questions are limited to {MAX_QUESTION} characters in the demo. Try a shorter one."
BAD_CODE = "That judge access code isn't right. Check it and try again, or continue as a visitor."
TOO_MANY_TRIES = "Too many wrong access codes. Try again in an hour, or continue as a visitor."
NO_JUDGES = "Judge access isn't set up on this server. Continue as a visitor."

_lock = threading.Lock()
_minute: dict[str, deque] = {}               # key -> request times in the last minute
_day: dict[str, tuple[str, int]] = {}        # key -> (Philadelphia date, count)
_slots = {pool: threading.BoundedSemaphore(max(p["slots"], 1)) for pool, p in POOLS.items()}


def _today() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d")


def _per_minute(key: str, limit: int) -> bool:
    now = time.monotonic()
    q = _minute.setdefault(key, deque())
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def _per_period(key: str, limit: int, period: str) -> bool:
    """At most `limit` in the period named `period` (a date, or a date and hour); a new period starts at 0."""
    seen, n = _day.get(key, (period, 0))
    n = n if seen == period else 0
    if n >= limit:
        return False
    _day[key] = (period, n + 1)
    return True


def _per_day(key: str, limit: int) -> bool:
    return _per_period(key, limit, _today())


def _prune() -> None:
    """Rotating addresses must not grow memory without bound: drop idle visitors."""
    if len(_minute) > 50_000:
        now = time.monotonic()
        for k in [k for k, q in _minute.items() if not q or now - q[-1] > 60]:
            del _minute[k]
    if len(_day) > 50_000:
        day = _today()
        for k in [k for k, (d, _) in _day.items() if d != day]:
            del _day[k]


_shown = 0


def client_ip(request) -> str:
    global _shown
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if _shown < 20 and request.url.path == "/agent":
        # What the deployment's proxies actually send decides the two settings above; the first few requests'
        # address headers go to the log so they can be set from evidence.
        _shown += 1
        seen = {h: request.headers[h] for h in _ADDRESS_HEADERS if h in request.headers}
        print(f"guardrails: address headers {seen} peer={request.client.host if request.client else None}",
              flush=True)
    if CLIENT_IP_HEADER and (v := request.headers.get(CLIENT_IP_HEADER, "").split(",")[-1].strip()):
        return v
    if TRUSTED_PROXIES and hops:
        return hops[-TRUSTED_PROXIES] if len(hops) >= TRUSTED_PROXIES else hops[0]
    return request.client.host if request.client else "unknown"


def check_request(ip: str, path: str) -> None:
    """Every route: a ceiling per visitor per minute, lower for /locate."""
    if path == "/health":
        return
    with _lock:
        _prune()
        if not _per_minute(f"req:{ip}", REQUESTS_PER_MINUTE):
            raise Limited(429, SLOW_DOWN, "visitor_minute")
        if path == "/locate" and not _per_minute(f"locate:{ip}", LOCATE_PER_MINUTE):
            raise Limited(429, SLOW_DOWN, "locate_minute")


def role(request, ip: str) -> str:
    """'judge' when the request carries the right access code (header X-PENN-Judge), 'visitor' when it carries
    none. A wrong code is refused, and only BAD_CODE_PER_HOUR wrong codes per visitor per hour are even checked.
    The page only decides what it shows; this is the check that counts, on every request."""
    code = request.headers.get("x-penn-judge", "").strip()
    if not code:
        return "visitor"
    if not JUDGE_CODE:
        raise Limited(404, NO_JUDGES, "no_judges")
    with _lock:
        if _day.get(f"badcode:{ip}", ("", 0)) == (_hour(), BAD_CODE_PER_HOUR):
            raise Limited(429, TOO_MANY_TRIES, "bad_code_limit")
    if hmac.compare_digest(code.encode(), JUDGE_CODE.encode()):
        return "judge"
    with _lock:
        _per_period(f"badcode:{ip}", BAD_CODE_PER_HOUR, _hour())
    raise Limited(401, BAD_CODE, "bad_code")


def _hour() -> str:
    return datetime.now(LOCAL_TZ).strftime("%Y-%m-%d %H")


def check_ask(ip: str, pool: str = "visitor") -> None:
    p = POOLS[pool]
    with _lock:
        if not _per_minute(f"ask:{pool}:{ip}", p["asks_minute"]):
            raise Limited(429, SLOW_DOWN, "visitor_minute")
        if not _per_day(f"ask:{pool}:{ip}", p["asks_day"]):
            raise Limited(429, DAY_IS_UP, "visitor_day")


def trim(question: str, history: list, context: dict | None) -> tuple[list, dict | None]:
    """Reject an over-long question; cut what else the request carries to what an honest page sends."""
    if len(question) > MAX_QUESTION:
        raise Limited(413, TOO_LONG, "too_long")
    for t in history:
        t.question, t.answer = t.question[:MAX_HISTORY_QUESTION], t.answer[:MAX_HISTORY_ANSWER]
    if context is not None and len(json.dumps(context, default=str)) > MAX_CONTEXT_BYTES:
        context = None                     # not something the page sends: answer without the carry-over
    return history[-MAX_HISTORY_TURNS:], context


@contextmanager
def fresh_answer(pool: str = "visitor"):
    """A new model answer from `pool`: counted against that pool's per-minute, per-hour and per-day ceilings, and
    run only while one of its slots is free. Counted when it starts, so a flood of abandoned requests still counts.
    The pools never share a count or a slot, so no number of visitors (or bots) can use up a judge's answers."""
    p = POOLS[pool]
    with _lock:
        if not _per_minute(f"fresh:{pool}", p["fresh_minute"]):
            raise Limited(503, BUSY, "minute_full")
        if _day.get(f"fresh:{pool}", ("", 0))[0] == _today() and _day[f"fresh:{pool}"][1] >= p["fresh_day"]:
            raise Limited(429, DEMO_FULL, "day_full")
        if not _per_period(f"fresh-hour:{pool}", p["fresh_hour"], _hour()):
            raise Limited(429, HOUR_FULL, "hour_full")
        _per_day(f"fresh:{pool}", p["fresh_day"])
    if not _slots[pool].acquire(blocking=False):
        raise Limited(503, BUSY, "busy")
    try:
        yield
    finally:
        _slots[pool].release()


# ---- the answer cache ----

_cache: dict[str, tuple[float, dict]] = {}    # key -> (stored at, epoch seconds; the response)
_keep_forever = bool(settings.pinned_now)     # the demo view: frozen data, so an answer stays right


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower()).rstrip(" ?.!")


def cache_key(question: str, lat, lon, history: list, context: dict | None) -> str:
    raw = json.dumps({"q": _norm(question),
                      "at": [round(lat, 4), round(lon, 4)] if lat is not None and lon is not None else None,
                      "h": [[_norm(t.question), t.answer[:300]] for t in history],
                      "c": context}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def cached(key: str) -> dict | None:
    with _lock:
        hit = _cache.get(key)
    if not hit:
        return None
    stored, response = hit
    if not _keep_forever and time.time() - stored > CACHE_MINUTES * 60:
        return None
    when = datetime.fromtimestamp(stored, LOCAL_TZ)
    return response | {"cached_at": when.isoformat(timespec="minutes")}


def store(key: str, response: dict) -> None:
    now = time.time()
    with _lock:
        if len(_cache) >= CACHE_MAX:
            for k, _ in sorted(_cache.items(), key=lambda kv: kv[1][0])[:CACHE_MAX // 10]:
                del _cache[k]
        _cache[key] = (now, response)
    if _keep_forever:
        try:
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with CACHE_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"key": key, "at": now, "response": response}, default=str) + "\n")
        except OSError:
            pass                             # the cache is a saving, never a reason to fail an answer


# ---- the usage log ----
# One line per Ask PENN request, in data/usage/<UTC date>.jsonl: what happened and what it cost, never what was
# asked (only its length) or who asked. Visitors appear as a keyed hash whose key is random and changes daily,
# so a day's repeat visitors can be counted but no address can be recovered. deploy/data_bucket.py push-usage
# turns finished days into summaries in the data bucket; `usage` prints them.

_salt: dict[str, bytes] = {}


def _visitor(ip: str, day: str) -> str:
    with _lock:
        if day not in _salt:
            _salt.clear()
            _salt[day] = os.urandom(16)
        key = _salt[day]
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()[:12]


def record(ip: str, outcome: str, started: float, question: str = "", response: dict | None = None,
           pool: str = "visitor") -> None:
    if not PUBLIC:
        return
    now = datetime.now(timezone.utc)
    calls = (response or {}).get("calls") or []
    row = {"at": now.isoformat(timespec="seconds"), "view": "demo" if settings.pinned_now else "live", "role": pool,
           "visitor": _visitor(ip, now.strftime("%Y-%m-%d")), "outcome": outcome, "chars": len(question),
           "model_calls": len(calls), "tokens_in": sum(c.get("prompt_tokens") or 0 for c in calls),
           "tokens_out": sum(c.get("output_tokens") or 0 for c in calls),
           "ms": int((time.monotonic() - started) * 1000)}
    try:
        USAGE_DIR.mkdir(parents=True, exist_ok=True)
        with _lock, (USAGE_DIR / f"{now:%Y-%m-%d}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass                                 # the log is for us; it never fails a visitor's request


def _load() -> None:
    if not (PUBLIC and _keep_forever and CACHE_FILE.exists()):
        return
    for line in CACHE_FILE.read_text(encoding="utf-8").splitlines()[-CACHE_MAX:]:
        try:
            row = json.loads(line)
            _cache[row["key"]] = (row["at"], row["response"])
        except (ValueError, KeyError):
            continue


_load()
