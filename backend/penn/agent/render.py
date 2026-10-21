"""The structured answer (PENN_STRUCTURED=1): the model picks the records, code writes the facts.

In the free-text loop the model writes every title, block, date and count, and loop.py checks and patches
the wording afterwards (the citation parser, the permit count, "is closed", the open-ended caveat, the
dates-only line). Each of those guards was added after the eval found the model getting a wording rule
wrong. Here the model ends with one submit_answer call (context.SUBMIT_ANSWER):

    {"direct_answer": "Yes, a storm water permit is in effect there.",
     "records": [{"event_id": "lane_closures:2025-07887", "why": "storm water work on Judson St"}],
     "no_record": false}

and this module writes the answer from the tool results: one line per record with its citation, same-title
permits on one block as one counted line, and the caveats the records call for. The model's own words are
the one-sentence direct answer, nothing else. Each record's `why` makes the model say why it picked it; it is
not shown.

First pass, on purpose: only records that carry a title (search_events in either shape, web pages) can be
written here. Trip briefs (answer_shape "brief"), find_event's editions and estimates and
get_location_history's years stay free text, checked by loop.py as before; a submit_answer that cites them
is sent back to answer in text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from . import citations, impact
from ..config import LOCAL_TZ

# The same sentences loop.py adds to a free-text answer (loop.OPEN_LINE, loop.PERMIT_LINE).
OPEN_LINE = "A permit with no end date listed may or may not be active: PENN cannot confirm whether work is happening."
PERMIT_LINE = "Permits list dates only, not daily hours or which days work happens."
PARKING_LINE = ("PENN only knows Temporary No Parking and Temporary Loading Zone permits; regular parking sign rules "
                "are not in PENN's data.")
PARTIAL_LINE = "This list is partial: more records matched than the search returned."
NO_RECORD_ANSWER = "PENN has no record that answers this."

SYSTEM = """
ANSWERING (submit_answer)
- When the answer rests on search_events records, or PENN has no record of what was asked, finish by
  calling submit_answer instead of writing the answer. You choose which records answer the question;
  code writes each one's title, blocks, dates, counts, caveats and citation.
- For a trip brief (answer_shape brief), an event (find_event), past years (get_location_history), or a
  reply with no records (a greeting, a question back to the user), answer in text as before.
"""

# Replaces loop.RULES["full"] when the answer is structured: the layout rules are code's job now.
FULL = """ANSWER SHAPE: FULL (code chose it for permits, closures, one block or an address)
- Answer with submit_answer, not text. Code writes every record's facts and the counts.
- records: every event_id that fits the question, on every street and block the search covered, not
  only the street the user named. An item with count and permits is several permits: list each event_id.
- direct_answer: one sentence (yes, no, or what kind of thing), with no titles, blocks, dates, counts or
  citations. Never say a street is closed.
- no_record true only when no record is that kind of thing; checked says what you looked for."""

RULES = {"full": FULL}
SUBMIT_NUDGE = ("This answer lists search_events records: call submit_answer instead, with the event_ids that answer "
                "the question and a one-sentence direct_answer. Code writes the records' facts.")

PARKING_QUESTION = re.compile(r"\bparking\b|\bpark (my|the|a|our)\b|\bwhere (can|do|should) (i|we) park\b|"
                              r"\bloading zones?\b|\bno[- ]parking\b", re.I)
_NO_RECORD_PREFIX = re.compile(r"^\s*(PENN has no records? (of|for)|no records? (of|for))\s+", re.I)
# "Judson St is closed": the direct answer is the model's own words, so the one wording rule left is
# checked here too. A sentence that says it is dropped and code's own first line stands alone.
_CLOSED_FACT = re.compile(r"\b(is|are|will be|will stay|will remain)\s+(now |still |fully |completely |partially )?"
                          r"closed\b", re.I)
_NEGATION = re.compile(r"\b(not|no|never|cannot|can[’']?t|whether|if)\b", re.I)
RECHECK_LIMIT = 30
MAX_BLOCKS = 8


def collect(obj) -> dict[str, dict]:
    """Every record with a title in a tool result, by event_id, with all its fields. A group's permits
    (context._for_model: {"title", "count", "permits": [...]}) each get the group's fields."""
    if isinstance(obj, list):
        return {k: v for x in obj for k, v in collect(x).items()}
    if not isinstance(obj, dict):
        return {}
    if isinstance(obj.get("permits"), list) and obj.get("title"):
        shared = {k: v for k, v in obj.items() if k not in ("permits", "count")}
        return {p["event_id"]: shared | p for p in obj["permits"]
                if isinstance(p, dict) and isinstance(p.get("event_id"), str)}
    if isinstance(obj.get("event_id"), str) and obj.get("title"):
        return {obj["event_id"]: dict(obj)}
    return {k: v for x in obj.values() for k, v in collect(x).items()}


@dataclass
class Rendered:
    text: str
    cited: list[str]


@dataclass
class Structured:
    """One turn's structured answer: the records the tools returned, and the submit_answer call checked
    and written. `open_question` says whether a "no record" answer must try the web first (loop.py's
    PERMIT_QUESTION decides, as for free text)."""
    question: str
    now: datetime
    open_question: bool = False
    records: dict[str, dict] = field(default_factory=dict)
    searched: dict = field(default_factory=dict)       # the last search_events "searched" block
    truncated: bool = False
    tries: int = 0                                     # submit_answer calls sent back so far
    rechecked: bool = False
    nudged_web: bool = False
    asked_submit: bool = False

    def see(self, result) -> None:
        """Called with every tool result."""
        self.records |= collect(result)
        if isinstance(result, dict) and isinstance(result.get("searched"), dict):
            self.searched = result["searched"]
            self.truncated = self.truncated or bool(result.get("truncated"))

    def wants_submit(self, shapes: list[str], tools_called: list[str]) -> bool:
        """A text answer that should have been submit_answer: the turn searched for a full record list and
        used no tool whose answer stays free text. Asked once."""
        if self.asked_submit or "full" not in shapes or "brief" in shapes:
            return False
        if any(t in tools_called for t in ("find_event", "get_location_history", "search_web", "submit_answer")):
            return False
        self.asked_submit = True
        return True

    def submit(self, args, returned_ids: set[str], web_searched: bool) -> Rendered | dict:
        """The answer, or an error result for the model to fix (each kind once, then the answer stands)."""
        if not isinstance(args, dict):
            return self._again("submit_answer arguments were not valid JSON")
        direct = str(args.get("direct_answer") or "").strip()
        ids = []
        for r in args.get("records") or []:
            i = r.get("event_id") if isinstance(r, dict) else r
            if isinstance(i, str) and i.strip():
                i = citations._resolve(i.strip().removeprefix("id:"), returned_ids)
                if i not in ids:
                    ids.append(i)
        unknown = [i for i in ids if i not in returned_ids]
        if unknown:
            if self.tries:
                ids = [i for i in ids if i not in unknown]       # second time: keep what the tools did return
                if not ids:
                    return Rendered("", [])                      # nothing supportable: loop gives up
            else:
                return self._again(f"cites event_ids the tools did not return: {unknown}. Use only event_ids "
                                   "copied from this turn's tool results")
        unwritable = [i for i in ids if i not in self.records]
        if unwritable:
            return {"error": f"submit_answer cannot write {unwritable}: they are event editions or past years, "
                             "not listed records. Answer in text instead, citing each as [id:<event_id>]."}
        if ids:
            return Rendered(self.render(direct, ids), self._expand(ids))
        if not args.get("no_record") and not self.records and not returned_ids:
            return Rendered(direct, [])                          # nothing was searched: a reply, not an answer
        if self.open_question and not web_searched and not self.nudged_web:
            self.nudged_web = True
            return {"error": "Before saying PENN has no record, call search_web with a short query of what the user "
                             "asked and where (not their whole message), then answer from what it finds."}
        if self.records and not self.rechecked:
            self.rechecked = True
            return {"error": self._recheck()}
        return Rendered(self.render_no_record(direct, str(args.get("checked") or "")), [])

    def _again(self, reason: str) -> dict:
        self.tries += 1
        return {"error": f"submit_answer {reason}. Call submit_answer again."}

    def _recheck(self) -> str:
        """A "no record" answer while the tools returned records: the model is shown them first, as
        loop._recheck does for text (a loading zone on the next street over answered the question)."""
        shown = list(self.records.items())[:RECHECK_LIMIT]
        lines = "\n".join(f"- {r['title']}, {str(r.get('description') or r.get('site') or '')[:90]} ({i})"
                          for i, r in shown)
        more = f"\n(and {len(self.records) - len(shown)} more)" if len(self.records) > len(shown) else ""
        return ("Before answering that PENN has no record, check each record the tools returned against the question. "
                "A record answers it if it is that kind of thing anywhere in the searched area: on a nearby street "
                f"too, and under a title that uses other words for it. The records:\n{lines}{more}\nCall submit_answer "
                "again with every one that answers the question; only if none does, with no_record true.")

    def _expand(self, ids: list[str]) -> list[str]:
        """The cited records plus the rest of each one's permit group: same title, block and occupancy are
        one line, and a line counts all of them."""
        out = []
        for i in ids:
            for j in self._group(i):
                if j not in out:
                    out.append(j)
        return out

    def _group(self, i: str) -> list[str]:
        r = self.records[i]
        if r.get("source") != "lane_closures" and not i.startswith("lane_closures:"):
            return [i]
        key = _key(r)
        return [j for j, s in self.records.items() if j.startswith("lane_closures:") and _key(s) == key]

    # Writing

    def render(self, direct: str, ids: list[str]) -> str:
        cited = self._expand(ids)
        lines: list[list[str]] = []                         # one bullet per record or permit group
        seen: set[str] = set()
        for i in cited:
            if i in seen:
                continue
            members = self._group(i)
            seen.update(members)
            lines.append(members)
        bullets = [self._line(m) for m in lines]
        head = self._headline(cited)
        out = [s for s in (self._direct(direct), head) if s] + [f"- {b}" for b in bullets]
        notes = []
        if self.truncated:
            notes.append(PARTIAL_LINE)
        recs = [self.records[i] for i in cited]
        if any(r.get("open_ended") for r in recs):
            notes.append(OPEN_LINE)
        if any(_is_permit(i, r) for i, r in zip(cited, recs)):
            notes.append(PERMIT_LINE)
        if PARKING_QUESTION.search(self.question):
            notes.append(PARKING_LINE)
        return "\n".join(out) + ("\n\n" + "\n".join(notes) if notes else "")

    def render_no_record(self, direct: str, checked: str) -> str:
        what = _NO_RECORD_PREFIX.sub("", checked.strip()).rstrip(". ")
        line = f"PENN has no record of {what}." if what else NO_RECORD_ANSWER
        place, window, kind = (self.searched.get(k) for k in ("place", "window", "window_kind"))
        out = [line]
        if direct and not citations.NO_RECORD.search(direct):
            out.insert(0, self._direct(direct))
        if window and kind == "future":
            out.append(f"PENN has no permits or announcements on record yet for {window}.")
        elif place and window:
            out.append(f"PENN searched {place}, {window}.")
        if PARKING_QUESTION.search(self.question):
            out.append(PARKING_LINE)
        return "\n".join(s for s in out if s)

    def _direct(self, direct: str) -> str:
        """The model's sentence, without citations (code writes them) and without "is closed" stated as a
        fact; empty when nothing is left."""
        text = re.sub(r"\s+", " ", citations.CITE.sub("", direct)).strip()
        text = re.sub(r"\s+([.,;!?])", r"\1", text)
        for m in _CLOSED_FACT.finditer(text):
            if not _NEGATION.search(text[:m.start()]):
                return ""
        return text

    def _headline(self, cited: list[str]) -> str:
        """'PENN found 3 permits and 1 announced closure for Judson St, Saturday Oct 10 morning:'."""
        counts: dict[str, int] = {}
        for i in cited:
            noun = _noun(i, self.records[i])
            counts[noun] = counts.get(noun, 0) + 1
        parts = [f"{n} {noun if n == 1 else noun + 's'}" for noun, n in counts.items()]
        what = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
        place, window = self.searched.get("place"), self.searched.get("window")
        where = f" for {place}, {window}" if place and window and set(counts) != {"web page"} else ""
        return f"PENN found {what}{where}:"

    def _line(self, members: list[str]) -> str:
        first = self.records[members[0]]
        i = members[0]
        if i.startswith("web:"):
            return _web_line(i, first)
        if len(members) > 1:
            dates = [f"{self._dates(self.records[j])} [id:{j}]" for j in members]
            listed = dates[0] if len(dates) == 1 else ", ".join(dates[:-1]) + " and " + dates[-1]
            parts = [f"{len(members)} {first['title']} permits", _blocks(first.get("description")),
                     _occupancy(first.get("occupancy"))]
            return ", ".join(p for p in parts if p) + f": permits in effect {listed}"
        cite = f"[id:{i}]"
        if _is_permit(i, first):
            parts = [first["title"], _blocks(first.get("description")), _occupancy(first.get("occupancy")),
                     "planned, not scheduled" if first.get("claim_certainty") == "planned"
                     else f"permit in effect {self._dates(first)}"]
            return ", ".join(p for p in parts if p) + f" {cite}"
        if i.startswith("event_closures:"):
            org = str(first.get("organization") or "")
            by = f" (closure list from {org}, not the City)" if org and "city of philadelphia" not in org.lower() else ""
            return f"{first['title']}, {self._when(first)}{by} {cite}"
        if i.startswith("philly_311:"):
            parts = ["311 report (a resident report, not a confirmed event)", first["title"],
                     _short(first.get("description")), self._when(first)]
            return ", ".join(p for p in parts if p) + f" {cite}"
        parts = [first["title"], _short(first.get("description")), self._when(first)]
        return ", ".join(p for p in parts if p) + f" {cite}"

    def _day(self, v) -> str:
        d = _date(v)
        if d is None:
            return ""
        return f"{d:%b} {d.day}" + (f", {d.year}" if d.year != self.now.year else "")

    def _dates(self, r: dict) -> str:
        """'Sep 10 to Nov 30', 'Oct 10 only', 'from May 27, 2024, no end date listed'."""
        s, e = r.get("start_date"), r.get("end_date")
        if not s:
            return self._when(r) or "dates not listed"
        if r.get("open_ended") or not e:
            return f"from {self._day(s)}, no end date listed"
        return f"{self._day(s)} only" if _date(s) == _date(e) else f"{self._day(s)} to {self._day(e)}"

    def _when(self, r: dict) -> str:
        """Announced times as Philadelphia local time, weekdays computed: 'Sat Oct 17, 7:00 AM – 12:00 PM'."""
        if not r.get("start_ts"):
            if r.get("start_date"):
                return impact.when(r)
            return ""
        s = datetime.fromisoformat(str(r["start_ts"])).astimezone(LOCAL_TZ)
        day = lambda d: f"{d:%a %b} {d.day}" + (f", {d.year}" if d.year != self.now.year else "")
        if not r.get("end_ts"):
            return f"{day(s)}, from {impact._clock(r['start_ts'])}"
        e = datetime.fromisoformat(str(r["end_ts"])).astimezone(LOCAL_TZ)
        if e.date() == s.date():
            return f"{day(s)}, {impact._clock(r['start_ts'])} – {impact._clock(r['end_ts'])}"
        return f"{day(s)} {impact._clock(r['start_ts'])} – {day(e)} {impact._clock(r['end_ts'])}"


def _key(r: dict) -> tuple:
    return r.get("title"), r.get("description"), r.get("occupancy")


def _is_permit(i: str, r: dict) -> bool:
    return i.startswith("lane_closures:") or r.get("source") == "lane_closures"


def _noun(i: str, r: dict) -> str:
    if _is_permit(i, r):
        return "permit"
    for prefix, noun in (("event_closures:", "announced closure"), ("philly_311:", "311 report"), ("web:", "web page")):
        if i.startswith(prefix):
            return noun
    return "listed event"


def _date(v) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


_BLOCK = re.compile(r"^\s*(\d+)\s+block of\s+(.+?)\s*$", re.I)


def _blocks(description) -> str:
    """'1300 block of LOCUST ST; 1400 block of LOCUST ST' -> '1300 block of Locust St and 1400 block of
    Locust St': each block as the permit lists it, never a range. A plan listing a hundred blocks is cut."""
    parts = []
    for chunk in str(description or "").split(";"):
        if not chunk.strip():
            continue
        m = _BLOCK.match(chunk)
        parts.append(f"{m.group(1)} block of {impact._pretty(m.group(2))}" if m else
                     (impact._pretty(chunk.strip()) if chunk.isupper() else chunk.strip()))
    if len(parts) > MAX_BLOCKS:
        rest = len(parts) - MAX_BLOCKS
        parts = parts[:MAX_BLOCKS] + [f"{rest} more block{'s' * (rest > 1)}"]
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1] if parts else ""


def _occupancy(occ) -> str:
    occ = str(occ or "").strip()
    if occ.lower() == "footway":
        return "Footway (the sidewalk only, not a traffic lane)"
    return occ if occ.lower() in ("full closure", "partial closure") else ""


def _short(text, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def _web_line(i: str, r: dict) -> str:
    """A web page in its own words: the excerpt quoted, never a date or street code did not read there."""
    site = r.get("site") or "the web"
    excerpt = _short(r.get("excerpt"), 220)
    return f"{r['title']}, according to {site}" + (f": “{excerpt}”" if excerpt else "") + f" [id:{i}]"
