"""Ask PENN: the tool-calling loop.  >>> YOURS TO BUILD <<<

See docs/LEARNING.md, steps 1 to 3 and 5. Tests that define "done":
tests/learning/test_loop.py (uses a fake client, so no API key is needed).

What is already decided for you:
- One model runs every turn (settings.model_chat, Super by default) and reads the
  conversation so far. Places and times are looked up by code through tools
  (penn/agent/context.py): the model passes words and gets short IDs back, so it
  never invents cells or dates. `ctx.call(name, args)` runs a tool, `SCHEMAS` is
  the list you pass to the API as `tools=`.
- `citations.collect_ids(result)` gathers IDs from a tool result, and
  `citations.check(text, ids)` tells you whether an answer is properly cited.
- Token Factory speaks the OpenAI chat-completions format, so the `openai`
  package works with `base_url=settings.token_factory_base_url`.

The loop you are building, in plain terms:
1. Send the system prompt, the earlier turns, and the question (plus the context).
2. If the reply asks for tool calls, run each tool, append its result as a
   "tool" message, and ask again.
3. If the reply is text, run the citation check. Accept it, or ask for one
   rewrite, then give up with an honest message.
4. Record one entry per model call (model, reason, latency, tokens) in `calls`.
"""
from __future__ import annotations
from openai import APITimeoutError, OpenAI
from dataclasses import dataclass, field
import json
import re
from penn.config import settings
from .context import SCHEMAS, SUBMIT_ANSWER, Context
from .router import Route
import time
from . import citations, render

SYSTEM = """You are PENN, a guide to street permits, street closures and events in the City of Philadelphia.
You answer from PENN's records first, and from the web (search_web) when they do not answer.
Each message ends with "Context": the date and time now, and the
places and windows already known this turn (place_id / window_id). Earlier messages are the
conversation so far.

SCOPE
- If the message is not about streets, getting around, permits, closures or events in Philadelphia
  (filler text, a test, a request to write something, small talk), do not call tools. Reply in one or
  two friendly sentences: what PENN can answer, and one example question. Answer a greeting with a
  greeting and the same offer.
- Open questions about Philadelphia are in scope, answered the way a search engine would: events,
  festivals, food, things to do, when something happens, "is there any X anywhere in Philadelphia?".
- PENN covers Philadelphia only. If resolve_place says a place is outside the city, say PENN does not
  cover it and stop.

FINDING THE PLACE AND TIME
- Code looks up every place and time; you never write cells, coordinates or dates into a tool call.
- this_place / this_window are the place and time code already found in this message. When listed,
  search them directly; do not resolve the same words again. resolve_place is for a place the
  message names that this_place does not cover, or a message with no this_place.
- Otherwise, ANY words that point at a place, however vague (a street, corner, address, neighborhood,
  landmark, building, park, plaza, "the art museum", "by the stadium"), go to resolve_place first.
  prev_place is only for a follow-up with no place words at all ("what about next week?", "and
  Sunday?"). Likewise prev_window only when the message has no time words ("and Spruce St?").
  pin is a point the user picked: use it when they say "here" or name no other place.
- If resolve_place cannot find the place, ask which street, corner or neighborhood they mean, or to
  pick a point on the map. Never fall back to prev_place for it.
- No place named, and no pin or prev_place: for a question about a street, a block or getting
  somewhere, ask where, without calling tools. A city-wide question ("anywhere in Philadelphia", "in
  Philly") or one about a kind of event ("any food festivals?") needs no place: call search_web.
- Time words go to resolve_window (only the time words). Event names ("Thanksgiving Day Parade",
  "the Mummers") are not times: never pass them to resolve_window.
- default_window only when the user names no time and there is no this_window or prev_window.
- Use the window exactly as resolved; never widen or move it. Say which window you used, using its
  label ("the next 7 days (Sat Oct 17 – Sat Oct 24)"); never compute weekdays yourself.
- If resolve_window returns an answer (before PENN's records), give that answer and stop.
- Earlier answers are context only: search again before stating any record, and cite only IDs the
  tools returned in this turn.

GROUNDING
- Every fact comes from a tool result. Cite it right after the fact, exactly like this:
  Install Cable, unit block of S 12th St, Partial Closure, permit in effect Sep 10 to Nov 30 [id:<event_id copied from the tool result>]
  Cite event_ids only, never a place_id or window_id. Never use other formats such as [citation: ...] or 【...】.
- Each event_id is a separate record. Never merge two records, even when their titles match.
  When asked how many, count distinct event_ids and say the number.
- If the user asks about one kind of thing (a block party, plumbing work, a parade) and no record is
  that kind, start with "PENN has no record of" it. Do not list unrelated records to fill the answer.
  Then, unless the question is about permits or street work, call search_web (see WEB) and answer
  from what it finds.
- List every record the tools returned that fits the question, on every street and block the search
  covered, not only the street the user named: "near 20th and Arch" includes N 20th St, S 19th St and
  the blocks around the corner. Never keep only a few records or only the closest one.
- For "what is around X" or "what is happening at X", list every relevant record the tools returned,
  including neighboring blocks. If truncated is true, say the list is partial.

RECORDS
- Timed records (start_ts, end_ts) are announced closures; their times are already Philadelphia
  local time. 311 records are resident reports, not confirmed events.
- If a record's organization names a race organizer, say the closure list came from the race
  organizer, not the City.
- Parking: PENN only knows Temporary No Parking and Temporary Loading Zone permits. Whenever parking
  comes up, say regular parking sign rules are not in PENN's data.

TIME
- Each window has a kind: current, past or future (search results repeat it as searched.window_kind).
- future: search and list what it returns, as for any window. Only if it returns no events, say
  "PENN has no permits or announcements on record yet for <window>." PENN adds past years' closures
  on the same dates below your answer itself; do not list past years unless asked.
- past: answer from what the records show. If nothing, say PENN has no record of permits or
  closures here for that window.

HISTORY
- For past years, whether something happens every year, what usually closes here, a named annual
  event, or whether a future event will close streets: call get_location_history with this_window
  (or default_window when there is none); it covers every year up to that window's end.
- past_events_here in a search result is background for those questions only. When the question
  is about past years or a recurring event, call get_location_history before answering; otherwise
  ignore past_events_here and answer about the window asked.

EVENTS (a question that names an event: "When is Rocky Run?", "Is the Marathon this weekend?")
- Call find_event with the event's name only. No place is needed: never ask for a pin or a place for an
  event question, and never search the default 7 days for "when is X?". If the context lists
  events_named, call find_event for it first.

WEB (search_web: a question PENN's records do not answer)
- Call it when the question is open or city-wide (a kind of event, "when does X happen", "does it ever
  have X"), or when the other tools found no record of what was asked. Never say "PENN has no record"
  as the whole answer before calling it. Pass a short query of what and where ("East Passyunk food
  festival", "food festivals October 2026"), never the user's whole message.

HOW TO ANSWER
- A tool result can end with how_to_answer: the rules for answering from that kind of result (describing
  permits, the answer's shape, events, web pages, past years). Follow them. Each set comes once per turn
  and still holds for later results of the same kind.
"""

# The rest of the prompt comes with the tool result it is about, the first time that kind of result comes
# back in a turn (rules_for). A trip question never needs the FULL shape and a permit question never needs
# the event or web rules, so the system prompt is about half as long; a rule next to the records it is
# about is also nearer the answer than one 150 lines back.
RULES = {
    "permits": """HOW TO DESCRIBE A PERMIT
- A permit is "a permit in effect", never a fact about the street. Never write that a street
  "is closed" or "is fully closed". Write "a permit listing a Full Closure is in effect".
- occupancy is what the permit lists: Full Closure, Partial Closure, or Footway. Footway means the
  sidewalk only: say it affects the sidewalk only, not a traffic lane.
- Permits give start and end dates only, never daily hours or which days work happens. When a permit's
  description itself states timing ("night-time", "overnight", "weekends", "second or third shift"),
  quote those words and say they come from the permit's description, not confirmed hours.
- open_ended true: give the start date, say no end date is listed, and say PENN cannot confirm
  whether work is happening. Never call it ongoing.
- claim_certainty "planned" is a city plan with no dates: say it is planned, not scheduled.""",

    "get_location_history": """PAST YEARS
- For a future event with no notice yet, say PENN has no announcement for it, then give past years
  as a pattern, not a promise.
- Cite one record for each year you mention, copying each edition's ready cite string exactly.
- Name the years PENN has records for; for "every year", say it cannot confirm other years.""",

    "find_event": """EVENTS
- An event at a place ("Was Broad St closed at Snyder Ave for the Broad Street Run?"): find_event's
  at_this_place says whether that place was on the route, from the closures that touch it. Answer from
  it, citing its edition; never decide from your own reading of a route's street limits.
- penn_records are City closure notices: start with them. Next date: if next.announced, give it. Else
  give next.estimates as estimates from past years, with their rule and support ("the second Saturday of
  November, as in 2024 and 2025"), then copy that estimate's cites string right after it (one cite per
  past year it rests on). When there are two, give both and say past years fit both. Never pick one
  silently, never present an estimate as announced.
- An estimate with confirmed_by: a web page lists that date. Give it as the date: "<site> lists <date>",
  copying its cite, then the past years behind it. Still say the City has not announced it yet.
  next.also_fits: dates past years would also allow; mention them in one short line, if at all.
- Past editions: date, closure hours (closures_from, reopen_by) and the main streets or route of the
  latest one (latest_route: where closures start and end, main streets, neighborhoods), citing each
  edition with its ready cite string exactly as given.
- web results: only when PENN has no notice. A date, time or street from a web result is cited
  [id:web:...] with "according to <site>", copied exactly as the excerpt writes it; never add a
  date, time or street the excerpt does not state. A web result never overrides a City notice: when
  they differ, give both and say which is the City's.
- penn_records null and no web results: say PENN has no record of the event and stop.""",

    "search_web": """WEB PAGES
- Answer like a search engine: the direct answer first (which festivals, when, where), one line per
  thing found, each cited [id:web:...] with "according to <site>". Copy dates, times and streets
  exactly as the excerpt writes them; never add one it does not state. Note each result's year when
  the page gives it, and say when a page may be from a past year.
- If City records came first ("PENN has no City record of a food festival closure in East Passyunk"),
  say that in one line, then give what the web says. A web page never overrides a City record.
- No web results either: then say PENN has no record of it, and found nothing on the web.
- Do not tell the user how to search; answer.""",

    "brief": """ANSWER SHAPE: BRIEF (code chose it for an area or a trip)
People use PENN to decide whether to go somewhere and how to get there. Give the takeaways; the map
  shows every record.
1. First line: whether getting there looks normal, and the exception with its day and time, cited.
   If getting_around.expected has an item, the first line warns about it, cited: "roadblocks" items as
   possible temporary roadblocks on its street during its time, because of that event; "crowds" items as
   crowds and Uber/Lyft cars stopping on its street. Never call such a day normal. Then, or else,
   if getting_around.new has any item, name its FIRST item (the biggest disruption); never say there
   are no closures when getting_around.new is not empty. If getting_around.lane_work exists, the first
   line also says to be mindful of lane work on its streets. Examples: "Getting to Rittenhouse next
   week looks normal except Sunday, October 25, when permits close Walnut Street from the 1400 to 1900
   blocks [id:...]." "You can drive to Fairmount on Sun Oct 25, but watch for temporary roadblocks on
   Fairmount Ave around the Halloween parade, 2:00 PM – 4:00 PM [id:...], and be mindful of lane work
   on Poplar St [id:...] [id:...]."
2. "Happening there": one line per event, with dates or times, cited.
3. "Getting around": getting_around.expected first. A "roadblocks" item: the event, its street and
   time, and that no City closure notice lists its blocks, so roadblocks are likely but not confirmed.
   A "crowds" item: one line, its street, expect crowds and rideshare drop-offs, copying its cite
   string. Then getting_around.new,
   one line each (a group is one line: what, streets, dates or times, its cite). Then ONE line for
   getting_around.lane_work: its streets and blocks, that lanes are narrowed but the street stays
   open, copying its cite string. Then ONE short line for getting_around.ongoing, naming the streets,
   citing each.
4. Last line: the minor count in words, e.g. "Plus 74 smaller permits (lane work, loading zones,
   sidewalk work) are on the map." Never list minor permits.
- Days and dates come from each item's "when" text, copied as written ("Sun Oct 25"); never work
  out a weekday yourself.
- If a closure group falls on an event's street and dates, say it is likely for that event, never certain.
- Keep it short: no more than about 10 lines.""",

    "full": """ANSWER SHAPE: FULL (code chose it for permits, closures, one block or an address)
- First line: a direct answer. Give the number of matching permits when asked how many, or when an
  item has count and permits: that item is several permits with one title on one block, so say
  "3 PGW gas leak permits are in effect on the 1000 block of New Market St". Add up items when the
  question covers several ("12 with excavation and 1 without: 13 gas leak permits").
- Then one bullet per record: title, blocks, occupancy, "permit in effect <start> to <end>", citation.
  Example: Temporary Valet Zone, 1300 block of Locust St and 1400 block of Locust St, Partial Closure, permit in effect Oct 10 only [id:...]
  For an item with permits, one bullet with the title, block and occupancy, then each permit's dates
  with its own citation: "permits in effect Jul 14 to Feb 23 [id:...], Jul 16 to Feb 27 [id:...] and Jul 17 to Feb 28 [id:...]".
- Write blocks as the description lists them ("1300 block of Locust St and 1400 block of Locust St"),
  never as a range like "1300-1400 blocks".
- Same start and end day reads "Oct 10 only".
- An announced closure (start_ts, end_ts): title, every street with its limits as written ("Broad St
  from Erie Ave to the Navy Yard"), date, times, citation. It has no occupancy; never write
  "occupancy not listed".
- For permits, end with: "Permits list dates only, not daily hours or which days work happens.\"""",
}


def _lines(text: str) -> list[str]:
    """A RULES section as JSON reads best: one string per heading, bullet or numbered item."""
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("  ") and out:
            out[-1] += " " + line.strip()
        elif line.strip():
            out.append(line.strip())
    return out


def rules_for(name: str, result) -> list[str]:
    """The RULES keys that go with this tool result: none for an error, a lookup or an ambiguous event."""
    if not isinstance(result, dict) or "error" in result:
        return []
    if name == "search_events":
        shape = (result.get("searched") or {}).get("answer_shape")
        return ["permits", shape] if shape in RULES else ["permits"]
    if name == "find_event":
        return [] if "matches" in result else ["find_event"]
    if name in ("search_web", "get_location_history"):
        return [name]
    return []

MAX_STEPS = 8   # resolve_place and resolve_window take a step before the search
# A "no record" answer while the web was not searched: sent back once to search_web, so an open question
# ("does East Passyunk ever have a food festival?") is not answered with City closure history alone.
# A window before PENN's records ("Its records start in 2016") is a fact about the records, not a miss.
NO_RECORD = re.compile(r"\bno records? (of|for|on)\b", re.I)
RECORDS_START = re.compile(r"\brecords start\b", re.I)
# Permits and street work are PENN's own records; the web adds nothing to "no plumbing permit on Spruce St".
PERMIT_QUESTION = re.compile(r"\b(permits?|permitted|block part(y|ies)|closed|closures?|construction|repav\w*|paving|excavat\w*|utility|pgw|peco|"
                             r"water main|sidewalks?|scaffold\w*|cranes?|lanes?|parking|loading|valet|311)\b", re.I)
WEB_NUDGE = ("Before saying PENN has no record, call search_web with a short query of what the user asked and "
             "where (not their whole message), then answer from what it finds, citing [id:web:...].")
GIVE_UP = "PENN could not produce an answer it can support with its records. Try a narrower question."
TIMED_OUT = "PENN's model took too long to answer. Try again in a moment."
CUT_OFF = ("Your answer was cut off at the length limit. Answer again, shorter: the same facts and citations, "
           "one line per record, no extra explanation.")


@dataclass
class Answer:
    text: str
    route: Route
    tools_called: list[str] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)
    request_id: str = ""
    calls: list[dict] = field(default_factory=list)
    groups: list[list[str]] = field(default_factory=list)   # brief groups' member IDs, so a cited group lights up whole
    checks: list[dict] = field(default_factory=list)        # drafts a check sent back: {"kind", "label"}, in order


# What each check caught, in words the answer's guard line shows (api.py `guard`).
SENT_BACK = {
    "unknown_record": "cited a record PENN never retrieved",
    "uncited": "stated facts without citing a record",
    "unreadable_cite": "had a citation PENN could not read",
    "web_fact": "stated a date, time or street its web page does not have",
    "closed_claim": "said a street is closed when only a permit is on file",
    "unsearched_no_record": "said \"no record\" before searching the web",
    "uncounted": "did not say how many permits",
}


def _sent_back(kind: str) -> dict:
    return {"kind": kind, "label": SENT_BACK[kind]}


def _failed(chk: citations.CitationCheck) -> str:
    """The SENT_BACK kind of a failed citation or web-fact check."""
    if chk.unknown:
        return "unknown_record"
    if chk.reason.startswith("has a citation"):
        return "unreadable_cite"
    if chk.reason.startswith("web-cited"):
        return "web_fact"
    return "uncited"


HISTORY_TURNS = 3
HISTORY_CHARS = 1200


def _past(answer: str) -> str:
    """An earlier answer as context: no citations (the model must search again, not copy IDs), no
    code-added notes, cut to a sane length."""
    text = citations.CITE.sub("", answer or "")
    text = "\n".join(l for l in text.splitlines() if not l.startswith("Note:")).strip()
    return text if len(text) <= HISTORY_CHARS else text[:HISTORY_CHARS].rstrip() + " …"


def _drop_handle_cites(text: str, ctx: Context) -> str:
    """'[id:this_place]' cites a handle, not a record. Drop it rather than fail an otherwise good
    answer for it (a correct "PENN has no record of a block party" often comes back with one)."""
    handles = set(ctx.places) | set(ctx.windows)

    def keep(m: re.Match) -> str:
        parts = m.group(1).removeprefix("id:").split(":")
        return "" if all(part in handles for part in parts) else m.group(0)

    out = citations.CITE.sub(keep, text)
    return re.sub(r"[ \t]+(?=[.,;])", "", out) if out != text else text


# "Permits list dates only, not daily hours or which days work happens": the FULL shape's closing line for
# permits. Event answers come from closure notices, which have times, so the model's habit of adding it
# there is wrong; the sentence is cut in code (prompt rules alone did not stop it).
_PERMIT_LINE = re.compile(r"[^.\n]*\b(?:permits?|PENN)\b[^.\n]*\b(?:daily (?:work )?hours|which (?:specific )?days)\b[^.\n]*\.?",
                          re.I)


def _drop_permit_line(text: str, tools_called: list[str]) -> str:
    if "find_event" not in tools_called or "search_events" in tools_called:
        return text
    out = _PERMIT_LINE.sub("", text)
    return re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", out)).strip()


PERMIT_LINE = "Permits list dates only, not daily hours or which days work happens."


def _add_permit_line(text: str, shapes: list[str]) -> str:
    """The FULL shape's closing line, added in code when an answer cites a permit and left it out: without
    it "in effect Oct 6 to Dec 31" reads as work every day (Super skipped it on the eval's sp05)."""
    if "full" not in shapes or _PERMIT_LINE.search(text):
        return text
    if not any(c.startswith("lane_closures:") for c in citations.CITE.findall(text)):
        return text
    return f"{text.rstrip()}\n\n{PERMIT_LINE}"


def records_seen(obj) -> dict[str, dict]:
    """Every record in a tool result by event_id: its title, where it is, and whether it is open-ended. A
    group's permits (context._for_model) each get the group's title."""
    if isinstance(obj, list):
        return {k: v for x in obj for k, v in records_seen(x).items()}
    if not isinstance(obj, dict):
        return {}
    if isinstance(obj.get("permits"), list) and obj.get("title"):
        return {p["event_id"]: {"title": obj["title"], "where": str(obj.get("description") or ""),
                                "open_ended": bool(p.get("open_ended") or obj.get("open_ended"))}
                for p in obj["permits"] if isinstance(p, dict) and isinstance(p.get("event_id"), str)}
    if isinstance(obj.get("event_id"), str) and obj.get("title"):
        return {obj["event_id"]: {"title": obj["title"], "where": str(obj.get("description") or ""),
                                  "open_ended": bool(obj.get("open_ended"))}}
    return {k: v for x in obj.values() for k, v in records_seen(x).items()}


RECHECK_LIMIT = 30


def _recheck(reason: str, records: dict[str, dict]) -> str:
    """The rewrite for an answer that cited nothing while the tools returned records. Offered a plain "no
    record" way out, the model took it with a record that answered the question in the results (a loading
    zone on the next street over; a repaving plan under another title). So it is shown the records first."""
    shown = list(records.items())[:RECHECK_LIMIT]
    lines = "\n".join(f"- {r['title']}, {r['where'][:90]} [id:{i}]" for i, r in shown)
    more = f"\n(and {len(records) - len(shown)} more)" if len(records) > len(shown) else ""
    return (f"Citation check failed: {reason}. Before answering that PENN has no record, check each record the tools "
            "returned against the question. A record answers it if it is that kind of thing anywhere in the searched "
            "area: on a nearby street too, and under a title that uses other words for it. The records:\n"
            f"{lines}{more}\nList every one that answers the question, cited as [id:<event_id>]. Only if none does, "
            "say in one sentence that PENN has no record of it.")


OPEN_LINE = "A permit with no end date listed may or may not be active: PENN cannot confirm whether work is happening."
_CANNOT_CONFIRM = re.compile(r"\b(cannot|can ?not|can[’']?t|unable to) confirm\b|\bmay or may not\b", re.I)


def _add_open_line(text: str, cited: set[str], records: dict[str, dict], shapes: list[str]) -> str:
    """Open-ended permits read as ongoing work unless the answer says otherwise; added in code when a full
    answer cites one and never says PENN cannot confirm it ("no end date listed, so it remains authorized")."""
    if "full" not in shapes or _CANNOT_CONFIRM.search(text):
        return text
    if not any(records.get(c, {}).get("open_ended") for c in cited):
        return text
    return f"{text.rstrip()}\n\n{OPEN_LINE}"


_IS_CLOSED = re.compile(r"\b(is|are) (now |still |fully |completely |partially )?closed\b", re.I)
_WILL_BE_CLOSED = re.compile(r"\bwill (be |stay |remain )?(fully |completely |partially )?closed\b", re.I)
_NEGATION = re.compile(r"\b(not|no|never|cannot|can[’']?t|whether|if)\b", re.I)


def closed_claims(text: str, cites_permits: bool, estimated: bool) -> list[str]:
    """Sentences that state a street is closed as a fact: "is closed" when the answer rests on permits (a
    permit is in effect, the street may not be closed), "will be closed" when the event's date is only an
    estimate. A negation earlier in the sentence ("not that the street is closed") does not count, nor does
    a sentence whose own cites are all City announcements ("3rd St is closed 10 AM to 2:15 PM [id:event_closures:...]")."""
    found = []
    for pattern, applies in ((_IS_CLOSED, cites_permits), (_WILL_BE_CLOSED, estimated)):
        if not applies:
            continue
        for m in pattern.finditer(text):
            start = max(text.rfind(".", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
            ends = [i for i in (text.find(".", m.end()), text.find("\n", m.end())) if i >= 0]
            sentence = text[start:min(ends) if ends else len(text)]
            own = citations.CITE.findall(sentence)
            if _NEGATION.search(text[start:m.start()]):
                continue
            if pattern is _IS_CLOSED and own and not any(c.startswith("lane_closures:") for c in own):
                continue                             # an announced closure, said as the City said it
            found.append(text[start:m.end()].strip())
    return found


HEDGE = ("Rewrite without stating that a street is or will be closed. A permit is \"a permit listing a Full Closure "
         "is in effect\", never a fact about the street; an event with no City notice yet is \"likely to close "
         "streets, as in past years, not confirmed\". Keep everything else the answer said, with its citations.")


_NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
                 10: "ten", 11: "eleven", 12: "twelve"}


def permit_groups(obj) -> list[tuple[int, set[str], str]]:
    """Items that fold several permits with one title on one block (context._for_model), as
    (count, their event_ids, title)."""
    if isinstance(obj, list):
        return [g for x in obj for g in permit_groups(x)]
    if not isinstance(obj, dict):
        return []
    own = []
    if isinstance(obj.get("permits"), list) and isinstance(obj.get("count"), int) and obj["count"] >= 2:
        ids = {p["event_id"] for p in obj["permits"] if isinstance(p, dict) and isinstance(p.get("event_id"), str)}
        own = [(obj["count"], ids, str(obj.get("title") or "permits"))]
    return own + [g for v in obj.values() for g in permit_groups(v)]


def _uncounted(text: str, cited: set[str], groups: list[tuple[int, set[str], str]]) -> list[tuple[int, set[str], str]]:
    """Cited groups whose number the answer never states ("3 PGW permits"). Listing three permits
    without the count reads as one permit with three dates (the eval's tr14)."""
    return [g for g in groups if g[1] & cited
            and not re.search(rf"\b({g[0]}|{_NUMBER_WORDS.get(g[0], g[0])})\b", text, re.I)]


def strip_think(text: str) -> str:
    """Reasoning models (Qwen3, DeepSeek, GLM, Kimi) can write their reasoning into the answer as
    <think>...</think>, or only the closing tag when the chat template opened it. The answer is what
    follows; left in, the reasoning's own draft cites would fail the citation check."""
    low = text.lower()
    if (i := low.rfind("</think>")) >= 0:
        text = text[i + len("</think>"):]
    elif (i := low.find("<think>")) >= 0:
        text = text[:i]                             # reasoning that never closed: it is not an answer
    return text.strip()


def chat_route(model: str | None = None) -> Route:
    model = model or settings.model_chat or settings.model_super
    name = "ultra" if model == settings.model_ultra else "super" if model == settings.model_super else "chat"
    return Route(model, name, f"Single model: {model.split('/')[-1]} for every turn")


def ask(question: str, ctx: Context, history: list[tuple[str, str]] | None = None, client=None,
        model: str | None = None, structured: bool | None = None) -> Answer:
    """Run one Ask PENN turn and return a cited answer. `ctx` holds the places and windows known so
    far and records which ones the answer searched; `history` is earlier (question, answer) pairs.
    `model` overrides settings.model_chat for this turn (the eval compares models with it).
    `structured` overrides settings.structured: record answers end with submit_answer, written by render.py."""
    client = client or OpenAI(base_url=settings.token_factory_base_url, api_key=settings.token_factory_api_key,
                              max_retries=1)    # the default 2 retries would let one hung call take 3 timeouts
    route = chat_route(model)
    sub = render.Structured(question, ctx.now, open_question=not PERMIT_QUESTION.search(question)) \
        if (settings.structured if structured is None else structured) else None
    rules, schemas = (RULES | render.RULES, SCHEMAS + [SUBMIT_ANSWER]) if sub else (RULES, SCHEMAS)
    messages = [{"role": "system", "content": SYSTEM + render.SYSTEM if sub else SYSTEM}]
    for q, a in (history or [])[-HISTORY_TURNS:]:
        messages += [{"role": "user", "content": q}, {"role": "assistant", "content": _past(a)}]
    messages.append({"role": "user", "content": f"{question}\n\nContext: {ctx.describe()}"})

    tools_called, calls = [], []
    returned_ids, groups = set(), []
    shapes, folded = [], []          # answer shapes code chose per search; permit groups the results folded
    rules_sent: set[str] = set()     # RULES already sent this turn
    records: dict[str, dict] = {}    # every record the tools returned: title, where, open-ended
    estimated = False                # find_event gave only an estimated date, no City notice
    retried = nudged = counted = hedged = False
    sent_back: list[dict] = []       # what each check caught, for the answer's guard line
    for _ in range(MAX_STEPS):
        t0 = time.monotonic()
        try:
            resp = client.chat.completions.create(model=route.model, messages=messages, temperature=0, tools=schemas,
                                                  max_tokens=settings.model_max_tokens,
                                                  timeout=settings.model_timeout_s)
        except APITimeoutError:
            calls.append({"model": route.model, "reason": route.reason, "error": "timeout",
                          "latency_ms": int((time.monotonic() - t0) * 1000), "prompt_tokens": 0, "output_tokens": 0})
            return Answer(TIMED_OUT, route, tools_called, [], "", calls, groups, sent_back)
        calls.append({
            "model": route.model,
            "reason": route.reason,
            "latency_ms": int((time.monotonic() - t0) * 1000),
            "prompt_tokens": resp.usage.prompt_tokens,
            "output_tokens": resp.usage.completion_tokens
        })
        msg = resp.choices[0].message

        if msg.tool_calls:
            messages.append(msg.model_dump(exclude_none=True))
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except ValueError:
                    args = None
                if sub and tc.function.name == "submit_answer":
                    result = sub.submit(args, returned_ids, ctx.web_searched)   # the answer, or what to fix
                    if isinstance(result, render.Rendered):
                        return Answer(result.text or GIVE_UP, route, tools_called + ["submit_answer"], result.cited,
                                      "", calls, groups, sent_back)
                    if "did not return" in str(result.get("error", "")):
                        sent_back.append(_sent_back("unknown_record"))
                else:
                    result = ctx.call(tc.function.name, args) if isinstance(args, dict) \
                        else {"error": "arguments were not valid JSON"}
                    if sub:
                        sub.see(result)
                returned_ids |= citations.collect_ids(result)
                groups += citations.collect_groups(result) + ctx.take_groups()
                folded += permit_groups(result)
                records |= records_seen(result)
                if isinstance(result, dict) and (shape := (result.get("searched") or {}).get("answer_shape")):
                    shapes.append(shape)
                if tc.function.name == "find_event" and isinstance(result, dict):
                    nxt = (result.get("penn_records") or {}).get("next") or {}
                    estimated = estimated or bool(nxt.get("estimates") and not nxt.get("announced"))
                tools_called.append(tc.function.name)
                new = [k for k in rules_for(tc.function.name, result) if k not in rules_sent]
                rules_sent.update(new)
                if new:
                    result = result | {"how_to_answer": [line for k in new for line in _lines(rules[k])]}
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str)
                })
            continue
        content = strip_think(msg.content or "")
        if getattr(resp.choices[0], "finish_reason", None) == "length":
            # Hit max_tokens: half an answer can still pass the citation check, so it is never accepted.
            if retried:
                break
            retried = True
            messages += [{"role": "assistant", "content": content}, {"role": "user", "content": CUT_OFF}]
            continue
        if sub and sub.wants_submit(shapes, tools_called):   # a record list written as text: ask once for submit_answer
            messages += [{"role": "assistant", "content": content}, {"role": "user", "content": render.SUBMIT_NUDGE}]
            continue
        text = _drop_handle_cites(citations.split_multi(content), ctx)   # one [id:...] per record from here on
        if retried:
            text = citations.drop_unread(text)   # an unreadable cite fails a first draft; a rewrite drops it instead
        if (not nudged and not ctx.web_searched and NO_RECORD.search(text) and not RECORDS_START.search(text)
                and not PERMIT_QUESTION.search(question)):
            nudged = True
            sent_back.append(_sent_back("unsearched_no_record"))
            messages += [{"role": "assistant", "content": content}, {"role": "user", "content": WEB_NUDGE}]
            continue
        chk = citations.check(text, returned_ids, allow_no_record=retried)   # plain "no record" only on the rewrite
        if chk.ok and ctx.web_pages:
            web = citations.web_facts(text, ctx.web_pages)   # web-cited dates, times and streets must be on the page
            chk = chk if web.ok else web
        if chk.ok and not counted and (missing := _uncounted(text, chk.cited, folded)):
            counted = True                           # asked once; a second miss is accepted, not failed
            sent_back.append(_sent_back("uncounted"))
            say = "; ".join(f'{n} "{title}" permits' for n, _, title in missing)
            messages += [{"role": "assistant", "content": content},
                         {"role": "user", "content": f"Say how many permits each group is, in the first line ({say}), "
                                                     "and the total when there are several groups. Keep everything "
                                                     "else the answer said, every record and its citation."}]
            continue
        if chk.ok and not hedged and closed_claims(
                text, any(c.startswith("lane_closures:") for c in chk.cited), estimated):
            hedged = True                            # asked once, like the count
            sent_back.append(_sent_back("closed_claim"))
            messages += [{"role": "assistant", "content": content}, {"role": "user", "content": HEDGE}]
            continue
        if chk.ok:
            text = _add_open_line(_drop_permit_line(text, tools_called), chk.cited, records, shapes)
            text = _add_permit_line(text, shapes)
            return Answer(text, route, tools_called, sorted(chk.cited), "", calls, groups, sent_back)
        sent_back.append(_sent_back(_failed(chk)))
        if retried:
            break                                    # second bad answer: give up
        retried = True
        messages.append({"role": "assistant", "content": content})
        if not chk.cited and records:
            messages.append({"role": "user", "content": _recheck(chk.reason, records)})
            continue
        messages.append({"role": "user",
                       "content": f"Citation check failed: {chk.reason}. Rewrite, citing each fact as [id:<event_id>] with square brackets, using only event_ids the tools returned (never a place_id or window_id). Include past_events_here or get_location_history records when they answer the question. If nothing the tools returned is relevant, answer in one sentence that PENN has no record of it."})

    return Answer(GIVE_UP, route, tools_called, [], "", calls, groups, sent_back)
