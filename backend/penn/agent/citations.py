"""Every fact in an answer must cite a record ID the tools actually returned.

The system prompt asks the model to cite as [id:<event_id>]. This check rejects
answers that cite unknown IDs, and answers that contain no citation while the
tools returned records.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# [id:<event_id>] as the prompt asks, or Nemotron's habits 【<event_id>】, 【{"id":"<event_id>"}】 and
# 【{"event_id":"<event_id>"}】, with or without "id:" in any case ("[ID: ...]" cost a right answer on the
# eval's tr12). A "<source>:" prefix inside the brackets is required, so made-up forms like
# [citation: ...] or [1] still fail the check. "ids:" counts as "id:".
CITE = re.compile(r'[\[【]\s*(?:\{\s*"(?:event_)?id"\s*:\s*")?(?:[iI][dD][sS]?:\s*)?([a-z0-9_]+:[A-Za-z0-9_:\-\.]+)"?\s*\}?\s*[\]】]')
# Several IDs in one bracket, 【ids:ticketmaster:A, ticketmaster:B】 or [id: a; b]: Super's habit when one line
# names two records. split_multi rewrites it as one [id:...] each, so everything that reads CITE sees a form
# it knows. The first ID needs its "<source>:" prefix; a bare one after it ("lane_closures:2026-1, 2026-2")
# takes the source before it.
_ONE = r"(?:[iI][dD]:\s*)?[a-z0-9_]+:[A-Za-z0-9_:\-\.]+"
_NEXT = r"(?:[iI][dD]:\s*)?[A-Za-z0-9_][A-Za-z0-9_:\-\.]*"
MULTI = re.compile(rf"[\[【]\s*(?:[iI][dD][sS]?\s*:\s*)?({_ONE}(?:\s*[,;]\s*{_NEXT})+)\s*[\]】]")
# A bracket that looks like a citation but that CITE cannot read would reach the user as raw text
# ("【ticketmaster】", "[ids: 2025-1]"), so it fails the check like an unknown ID.
UNREAD = re.compile(r"【\s*[^\s】][^】]*】|\[\s*ids?\s*:[^\]]*\]", re.I)
SOURCES = ("lane_closures:", "event_closures:", "philly_311:", "visitphilly:", "ticketmaster:", "web:")
# A correct negative answer ("PENN has no record of a block party there") has nothing to cite even when
# the tools returned other records for the area. Allowed uncited only if it states no specifics.
NO_RECORD = re.compile(r"\b(PENN has no record|PENN has no records|no record of|no records of|PENN does not cover)\b", re.I)
# Specifics = a block number or a date range; the place name and the question's own date are not.
SPECIFICS = re.compile(r"\b\d{1,5}\s+block\b|\b(?:until|through)\s+\w"
                       r"|\bto\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d", re.I)


def split_multi(text: str) -> str:
    """'【ids:a:1, a:2】' -> '[id:a:1] [id:a:2]'. Text with no multi-ID bracket comes back unchanged."""
    def one_each(m: re.Match) -> str:
        out, source = [], ""
        for p in re.split(r"\s*[,;]\s*", m.group(1)):
            p = re.sub(r"^[iI][dD]:\s*", "", p)
            if ":" in p:
                source = p.split(":", 1)[0]
            else:
                p = f"{source}:{p}"
            out.append(f"[id:{p}]")
        return " ".join(out)
    return MULTI.sub(one_each, text)


def drop_unread(text: str) -> str:
    """Cut the brackets UNREAD finds, for a rewrite that still has one: the rest of the answer can stand."""
    def drop(m: re.Match) -> str:
        return "" if UNREAD.fullmatch(CITE.sub("", m.group(0)).strip()) else m.group(0)
    out = UNREAD.sub(drop, text)
    return re.sub(r"[ \t]+(?=[.,;])", "", out) if out != text else text


def _resolve(c: str, returned: set[str]) -> str:
    """Map a cited ID to the returned one it names: full IDs stay; "id:EUN2446475" or a bare permit
    number counts only when exactly one returned ID ends with it."""
    if c in returned or c.startswith(SOURCES):
        return c
    bare = c.split(":", 1)[1] if ":" in c else c
    hits = [r for r in returned if r.endswith(":" + bare)]
    return hits[0] if len(hits) == 1 else c

def collect_ids(obj) -> set[str]:
    """Walk a tool result and gather every event_id it contains."""
    ids: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "event_id" and isinstance(v, str):
                ids.add(v)
            elif k in ("scheduled_now", "event_ids") and isinstance(v, list):
                ids.update(x for x in v if isinstance(x, str))
            elif k == "by_occupancy" and isinstance(v, dict):
                for lst in v.values():
                    ids.update(lst)
            else:
                ids |= collect_ids(v)
    elif isinstance(obj, list):
        for x in obj:
            ids |= collect_ids(x)
    return ids


def collect_groups(obj) -> list[list[str]]:
    """Every group's member IDs in a tool result (penn.agent.impact groups carry event_ids)."""
    if isinstance(obj, dict):
        own = [obj["event_ids"]] if isinstance(obj.get("event_ids"), list) else []
        return own + [g for v in obj.values() for g in collect_groups(v)]
    if isinstance(obj, list):
        return [g for x in obj for g in collect_groups(x)]
    return []


def expand_groups(cited: list[str], groups: list[list[str]]) -> list[str]:
    """Cited IDs plus every other member of a group any of them belongs to, no repeats: the cited first, then
    the groups in the order the tools gave them (an event's editions latest first), whatever order the answer
    cites them in. The map lights only the first few dozen, so the latest route must come first."""
    out, cites = list(cited), set(cited)
    for g in groups:
        if cites.intersection(g):
            out += [i for i in g if i not in out]
    return out


@dataclass
class CitationCheck:
    ok: bool
    cited: set[str] = field(default_factory=set)
    unknown: set[str] = field(default_factory=set)
    reason: str = ""


def check(answer: str, returned_ids: set[str], allow_no_record: bool = False) -> CitationCheck:
    """allow_no_record: accept a plain uncited "PENN has no record" answer. The loop passes it only
    on the rewrite, so a lazy first "no record" is still pushed to cite what the tools found."""
    answer = split_multi(answer)
    cited = {_resolve(c, returned_ids) for c in CITE.findall(answer)}
    unknown = cited - returned_ids
    if unknown:
        return CitationCheck(False, cited, unknown, f"cites IDs the tools did not return: {sorted(unknown)}")
    if unread := UNREAD.search(CITE.sub("", answer)):
        return CitationCheck(False, cited, set(), f"has a citation PENN cannot read: {unread.group(0)}")
    if returned_ids and not cited:
        if allow_no_record and NO_RECORD.search(answer) and not SPECIFICS.search(answer):
            return CitationCheck(True, cited, set(), "")      # a plain "no record" answer
        return CitationCheck(False, cited, set(), "tools returned records but the answer cites none")
    return CitationCheck(True, cited, set(), "")


# Web facts (p5-09): a line that cites only web pages may state a date, a time or a street only if that
# page's text says it. The model reads short excerpts, so it can misremember or fill in; this catches it.
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DATE = re.compile(rf"\b{_MONTH_RE}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s?m\b\.?", re.I)
_STREET = re.compile(r"\b((?:[A-Z][a-z]+|\d+(?:st|nd|rd|th))(?:\s+[A-Z][a-z]+)*)\s+(St|Street|Ave|Avenue|Dr|Drive|Blvd|"
                     r"Boulevard|Rd|Road|Pkwy|Parkway|Way|Lane|Ln)\b\.?")
_ABBR = {"st": "street", "ave": "avenue", "dr": "drive", "blvd": "boulevard", "rd": "road", "pkwy": "parkway", "ln": "lane"}
WEB_CITE = re.compile(r"[\[【]\s*(?:[iI][dD]:)?\s*(web:[0-9a-f]{8})\s*[\]】]")   # 【id:web:...】 too, as CITE reads it


def _month(word: str) -> str:
    return next(m for m in _MONTHS if m.startswith(word.lower()[:3]))


def _plain(text: str) -> str:
    """Lower case, abbreviations spelled out, punctuation dropped, times as '7 am' / '8:30 am'."""
    t = text.lower()
    t = _DATE.sub(lambda m: f"{_month(m.group(1))} {int(m.group(2))}", t)
    t = _TIME.sub(lambda m: f"{int(m.group(1))}{':' + m.group(2) if m.group(2) and m.group(2) != '00' else ''} {m.group(3).lower()}m", t)
    t = re.sub(r"[^a-z0-9: ]+", " ", t)
    return " " + " ".join(_ABBR.get(w, w) for w in t.split()) + " "


def _facts(line: str) -> list[str]:
    facts = [f"{_month(m.group(1))} {int(m.group(2))}" for m in _DATE.finditer(line)]
    facts += [_plain(m.group(0)).strip() for m in _TIME.finditer(line)]
    facts += [_plain(m.group(0)).strip() for m in _STREET.finditer(line)]
    return facts


def web_facts(answer: str, pages: dict[str, dict]) -> CitationCheck:
    """Every date, time and street in a line citing only web pages must appear in one of those pages."""
    missing = []
    for line in split_multi(answer).splitlines():
        ids = WEB_CITE.findall(line)
        if not ids or len(CITE.findall(line)) > len(ids):        # no web cite, or mixed with PENN records
            continue
        text = " ".join(_plain(pages[i].get("text") or pages[i].get("excerpt") or "") for i in ids if i in pages)
        body = WEB_CITE.sub("", line)
        missing += [f for f in _facts(body) if f" {f} " not in text]
    if missing:
        return CitationCheck(False, set(), set(), "web-cited lines state facts their pages do not: " + ", ".join(missing[:6])
                             + ". Copy dates, times and streets from the page, or drop them.")
    return CitationCheck(True, set(), set(), "")
