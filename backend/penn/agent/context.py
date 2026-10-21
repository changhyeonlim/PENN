"""One turn's places and time windows, handed to the model as short IDs.

The model reads the conversation and decides what the user means ("Spruce St", "what about next
week?", the point they picked); code still computes every place and window. A neighborhood is
hundreds of H3 cells, far too many for the model to copy between tool calls, so each resolved
place or window is stored here and the model only ever sees its ID:

    resolve_place("Spruce St")      -> {"place_id": "p1", "label": ...}   (penn.geo.locate)
    resolve_window("next week")     -> {"window_id": "w1", "label": ...}  (penn.timeutil)
    search_events("p1", "w1")       -> the records, searched with p1's cells and legs

Some IDs exist before the model runs: `pin` (a point the user picked), `prev_place` and
`prev_window` (what the last answer used), `default_window` (the next 7 days). An unknown ID is an
error, so the model cannot invent a place.
"""
from __future__ import annotations

import inspect
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .. import timeutil, tools
from . import events as event_lookup
from ..web import tavily
from ..geo import locate
from ..timeutil import Window


@dataclass
class Place:
    id: str
    label: str
    loc: locate.Resolved
    query: dict            # how to find it again next turn: {"text": ...} or {"lat": .., "lon": ..}
    note: str = ""         # what the model is told about it


@dataclass
class Win:
    id: str
    window: Window
    kind: str              # current | past | future | before_records
    note: str = ""

    @property
    def label(self) -> str:
        return self.window.label


def _span_label(a: datetime, b: datetime) -> str:
    """'Sat Oct 17 – Sat Oct 24', weekdays computed, never written by the model."""
    return f"{a:%a %b} {a.day} – {b:%a %b} {b.day}"


@dataclass
class Context:
    now: datetime
    records_start: date
    places: dict[str, Place] = field(default_factory=dict)
    windows: dict[str, Win] = field(default_factory=dict)
    searches: list[tuple[str, str]] = field(default_factory=list)   # (place_id, window_id) per search, in order
    unresolved: list[str] = field(default_factory=list)             # place texts resolve_place could not find
    message: str = ""                                               # this turn's message, set by add_message
    event_names: list[str] = field(default_factory=list)            # catalog events this message names
    web_pages: dict[str, dict] = field(default_factory=dict)        # web id -> page, for citations.web_facts
    groups: list[list[str]] = field(default_factory=list)           # record groups kept from the model (take_groups)
    web_searched: bool = False                                      # find_event or search_web asked the web this turn

    def __post_init__(self):
        start = self.now
        end = start + timedelta(days=7)
        self.add_window(Window(start, end, f"the next 7 days ({_span_label(start, end)})"), "default_window",
                        note="use only when the user names no time and none came before")

    # Registering

    def _kind(self, w: Window) -> str:
        return timeutil.window_kind(w, self.now, self.records_start)

    def add_window(self, w: Window, id_: str | None = None, note: str = "") -> Win:
        id_ = id_ or f"w{sum(1 for k in self.windows if k.startswith('w') and k[1:].isdigit()) + 1}"
        win = Win(id_, w, self._kind(w), note)
        self.windows[id_] = win
        return win

    def add_place(self, loc: locate.Resolved, query: dict, id_: str | None = None, note: str = "") -> Place:
        id_ = id_ or f"p{sum(1 for k in self.places if k.startswith('p') and k[1:].isdigit()) + 1}"
        place = Place(id_, loc.label, loc, query, note)
        self.places[id_] = place
        return place

    def add_pin(self, lon: float, lat: float) -> locate.Resolved:
        loc = locate.from_click(lon, lat)
        if loc.method != "outside":
            self.add_place(loc, {"lat": lat, "lon": lon}, "pin", note="the point the user picked on the map")
        return loc

    def add_previous(self, prev: dict | None) -> None:
        """Last answer's place and window, sent back by the frontend, resolved again here."""
        prev = prev or {}
        if q := prev.get("place"):
            if "lat" in q and "lon" in q:
                loc = locate.from_click(q["lon"], q["lat"])
            else:
                loc = locate.from_text(q.get("text", ""))
            if loc.cells:
                self.add_place(loc, q, "prev_place", note="the place the last answer used")
        if w := prev.get("window"):
            try:
                win = Window(datetime.fromisoformat(w["start"]), datetime.fromisoformat(w["end"]), w["label"])
                self.add_window(win, "prev_window", note="the time the last answer used")
            except (KeyError, ValueError):
                pass

    # What the model is told

    def describe(self) -> str:
        return json.dumps({
            "now": f"{self.now:%A, %B} {self.now.day}, {self.now.year} {self.now:%H:%M}",
            **({"events_named": self.event_names,
                "events_note": "this message names an event PENN knows: call find_event with its name; no place is "
                               "needed"} if self.event_names else {}),
            "places": [{"place_id": p.id, "label": p.label, "note": p.note} for p in self.places.values()],
            "windows": [{"window_id": w.id, "label": w.label, "kind": w.kind, "note": w.note}
                        for w in self.windows.values()],
        })

    # What the answer used

    def last_place(self) -> Place | None:
        return self.places[self.searches[-1][0]] if self.searches else None

    def last_window(self) -> Win | None:
        return self.windows[self.searches[-1][1]] if self.searches else None

    def used(self, id_: str) -> bool:
        return any(id_ in s for s in self.searches)

    # Tools

    def add_message(self, question: str) -> None:
        """Look the whole message up once, as the pipeline always did: a corner or neighborhood in
        the user's own words is found far more reliably from the full text than from a fragment the
        model passes to resolve_place ("23rd Street near Market Street" finds nothing)."""
        self.message = question
        loc = locate.from_text(question)
        if loc.cells and loc.method != "outside":
            self.add_place(loc, {"text": question}, "this_place",
                           note="the place in this message, already looked up")
        if w := timeutil.resolve_window(question, self.now):
            self.add_window(w, "this_window", note="the time in this message, already looked up")
        cat = tools.history.catalog()
        self.event_names = [cat[s]["name"] for s in event_lookup.match_event(question, cat)]

    def _is_this_place(self, text: str, loc: locate.Resolved) -> bool:
        """Whether a place the model looked up is this message's place, which code already found from
        the whole message. The whole message wins: it is what the pipeline always searched, and the
        model's fragments find nothing ("Spruce Street"), or the wrong place ("Ludlow Street between
        18th and 19th" matches the Ludlow neighborhood). Only a second place the message names, one
        the rest of the message still finds without these words, gets its own place_id."""
        here = self.places.get("this_place")
        if here is None:
            return False
        if not loc.cells or set(loc.cells) & set(here.loc.cells):
            return True
        rest = _without(self.message, text)
        return bool(self.message) and not (rest and locate.from_text(rest).cells)

    def resolve_place(self, text: str) -> dict:
        loc = locate.from_text(text)
        if loc.method == "outside":
            return {"outside": True, "label": loc.label,
                    "message": f"{loc.label} is outside the City of Philadelphia; PENN has no records there."}
        if self._is_this_place(text, loc):
            here = self.places["this_place"]
            return {"place_id": here.id, "label": here.label, "found": True,
                    "next": f"Found: {text!r} is inside this_place, the area code already looked up from the "
                            f"whole message. Search place_id {here.id!r} now; do not ask the user where."}
        if not loc.cells:
            self.unresolved.append(text)
            return {"found": False, "text": text,
                    "next": "Ask the user which street, corner or neighborhood they mean, or to pick a point on "
                            "the map. Do not use prev_place for a place the user named."}
        p = self.add_place(loc, {"text": text})
        return {"place_id": p.id, "label": p.label, "method": loc.method}

    def resolve_window(self, text: str) -> dict:
        w = timeutil.resolve_window(text, self.now)
        if w is None:
            use = "this_window" if "this_window" in self.windows else "default_window"
            return {"found": False, "text": text,
                    "next": f"PENN reads dates, not event names. For past years, 'every year' or a named event, call "
                            f"get_location_history with {use}: it covers every year up to that window's end. "
                            f"Otherwise search {use} and tell the user PENN read no date in their words."}
        win = self.add_window(w)
        out = {"window_id": win.id, "label": win.label, "kind": win.kind}
        if win.kind == "before_records":
            out["answer"] = timeutil.before_records_answer(win.label, self.records_start)
        return out

    def _handles(self, place_id: str, window_id: str) -> tuple[Place, Win] | dict:
        if place_id not in self.places:
            return {"error": f"unknown place_id {place_id!r}; call resolve_place or use one listed in the context"}
        if window_id not in self.windows:
            return {"error": f"unknown window_id {window_id!r}; call resolve_window or use one listed in the context"}
        win = self.windows[window_id]
        if win.kind == "before_records":
            return {"error": "no records for this window",
                    "answer": timeutil.before_records_answer(win.label, self.records_start)}
        return self.places[place_id], win

    def search_events(self, place_id: str, window_id: str, include_311: bool = False) -> dict:
        """Every kind of record, as the pipeline always searched: a type filter chosen by the model hid
        the records that answered the question (a "construction" filter dropped the ADA ramps and bus
        shelters). Brief or full is chosen here from the message, not by the model."""
        h = self._handles(place_id, window_id)
        if isinstance(h, dict):
            return h
        place, win = h
        detail = choose_detail(self.message, place.loc.method)
        max_days = short_term_days(self.message)
        token = tools.LEGS.set(place.loc.seg_ids or None)   # exact legs when a corner was named
        try:
            out = tools.search_events(place.loc.cells, win.window.start.isoformat(), win.window.end.isoformat(),
                                      include_311=include_311, detail=detail, max_days=max_days)
        finally:
            tools.LEGS.reset(token)
        self.searches.append((place_id, window_id))
        if detail == "full" and isinstance(out.get("events"), list):
            out = _for_model(out)
        if isinstance(out.get("past_events_here"), dict):
            out = out | {"past_events_here": _past_names(out["past_events_here"])}
        searched = {"place": place.label, "window": win.label, "window_kind": win.kind, "answer_shape": detail}
        if max_days:
            searched["max_days"] = max_days
            searched["note"] = (f"Only records lasting {max_days} day{'s' * (max_days > 1)} or less, as the question "
                                "asks: list every one of them.")
        return out | {"searched": searched}

    def get_location_history(self, place_id: str, window_id: str, since_year: int = 2016) -> dict:
        h = self._handles(place_id, window_id)
        if isinstance(h, dict):
            return h
        place, win = h
        token = tools.LEGS.set(place.loc.seg_ids or None)
        try:
            return tools.get_location_history(place.loc.cells, win.window.start.isoformat(),
                                              win.window.end.isoformat(), since_year)
        finally:
            tools.LEGS.reset(token)

    def find_event(self, name: str) -> dict:
        """An event by name, city-wide: PENN's past editions from City notices, the next date, the latest
        route. The web (Tavily) is searched only when PENN has no such event, or no notice for its coming
        edition; only the event's name and a year are sent."""
        cat = tools.history.catalog()
        series = event_lookup.match_event(name, cat) or event_lookup.match_event(self.message, cat)
        if len(series) > 1:
            return {"matches": [cat[s]["name"] for s in series],
                    "next": "Several events match. Ask the user which one they mean."}
        today = self.now.date()
        out: dict = {"event": name}
        year = today.year
        if series:
            h = event_lookup.series_history(series[0], today)
            # Older editions as dates, hours and cites; the closures of the latest one only.
            eds = h["editions"]
            # Each edition's whole route stays server side (a Marathon edition is ~80 ids): citing the
            # edition lights every closure on the map through these groups.
            self.groups += [e["event_ids"] for e in eds if len(e.get("event_ids") or []) > 1]
            h["editions"] = [{k: v for k, v in e.items() if k != "event_ids" and (k != "closures" or i == len(eds) - 1)}
                             for i, e in enumerate(eds)]
            out = {"event": h["name"], "penn_records": h}
            if "this_place" in self.places:
                out["at_this_place"] = self._event_at_place(series[0])
            if h["next"].get("announced"):
                return out
            if h["next"].get("estimates"):
                year = int(h["next"]["estimates"][0]["date"][:4])
            sites = [s for s in (cat[series[0]].get("sites") or "").split("|") if s]
            query_name = event_lookup.short_name(series[0], f"{name} {self.message}", cat)
        else:
            out["penn_records"] = None
            sites, query_name = [], name
        web = tavily.search(query_name, year, sites)
        self.web_searched = True
        for r in web["results"]:
            self.web_pages[r["event_id"]] = r
        if out.get("penn_records") and web["results"]:
            # Code, not the model, decides which estimate a web page backs: the model passed over the
            # organizer's "November 14" when it was only one of five excerpts.
            event_lookup.confirm_estimates(out["penn_records"]["next"], web["results"], query_name)
        out["web"] = {"query": web["query"], **({"note": web["note"]} if "note" in web else {}),
                      "results": [{k: v for k, v in r.items() if k != "text"} for r in web["results"]]}
        return out

    def _event_at_place(self, series: str) -> dict:
        """Whether this message's place was on the event's route, from the event's closures that touch
        the place. Left to the model, it read "Broad St from Erie Ave to the Navy Yard" and decided
        Snyder Ave was not on it."""
        place = self.places["this_place"]
        # Through today and through the question's own window: "this year's race" asked at 6 AM on race
        # day must see that day's closures.
        end = self.now + timedelta(days=1)
        if "this_window" in self.windows:
            end = max(end, self.windows["this_window"].window.end)
        token = tools.LEGS.set(place.loc.seg_ids or None)
        try:
            hist = tools.get_location_history(place.loc.cells, (end - timedelta(days=1)).isoformat(), end.isoformat())
        finally:
            tools.LEGS.reset(token)
        match = next((s for s in hist.get("events", []) if s.get("series") == series), None)
        if not match:
            return {"place": place.label, "on_route": False,
                    "note": f"None of this event's closures in PENN's records touch {place.label}."}
        return {"place": place.label, "on_route": True, "editions": match["editions"][-3:],
                "note": f"These closures of the event touch {place.label}: it was on the route. Cite the edition."}

    def search_web(self, query: str) -> dict:
        """An open question PENN's records do not answer ("any food festival in Philadelphia?", "does
        East Passyunk have a food festival?"): web pages for the model's short query, each citable as
        web:<id>. Only the query leaves PENN, never the user's message."""
        web = tavily.search_open(query)
        self.web_searched = True
        for r in web["results"]:
            self.web_pages[r["event_id"]] = r
        return {"query": web["query"], **({"note": web["note"]} if "note" in web else {}),
                "results": [{k: v for k, v in r.items() if k != "text"} for r in web["results"]],
                "source": "the web, not City records: cite each fact [id:web:...] with 'according to <site>'"}

    def take_groups(self) -> list[list[str]]:
        """Record groups a tool kept out of its result (see find_event); each is handed out once."""
        out, self.groups = self.groups, []
        return out

    def call(self, name: str, args: dict) -> dict:
        """Run one tool call from the model; a bad call comes back as an error the model can read."""
        fn = {"resolve_place": self.resolve_place, "resolve_window": self.resolve_window,
              "search_events": self.search_events, "get_location_history": self.get_location_history,
              "find_event": self.find_event, "search_web": self.search_web}.get(name)
        if fn is None:
            return {"error": f"no tool named {name!r}"}
        # Forgive what the model gets wrong most: made-up arguments ("query": "race") are dropped, and a
        # search with no place_id or window_id uses this message's own, when code found one.
        params = inspect.signature(fn).parameters
        args = {k: v for k, v in args.items() if k in params}
        for key, own in (("place_id", "this_place"), ("window_id", "this_window")):
            if key in params and key not in args and own in (self.places if key == "place_id" else self.windows):
                args[key] = own
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"bad arguments for {name}: {e}"}


# A trip: the person is deciding whether and how to go, so the takeaways matter, not every permit.
_TRIP = re.compile(r"\b(get(ting)? (to|there|in|into|around|out)|driv(e|ing)|head(ing)? (to|over|down|up)|"
                   r"go(ing)? (to|there|over)|trip|visit(ing)?)\b", re.I)
# Asking about permits or one block: list every record.
_DETAIL = re.compile(r"\b(permits?|permitted|blocks?|\d{2,5} ([nsew]\.? |north |south )?\w+ "
                     r"(st|street|ave|avenue|rd|road|ln|lane|pl|place|blvd|boulevard|dr|drive)|park(ing)?|loading|valet|"
                     r"construction|repav\w*|paving|dig|excavat\w*|crews?|work|how many|records?|utility|gas|peco|"
                     r"pgw|water department|septa|sidewalks?|footway|scaffold\w*|shed|crane|hoist|manholes?|"
                     r"restoration|generator|closed|closures?|blocked|lanes?)\b", re.I)


_ONE_DAY = re.compile(r"\b(one|1|single)[- ]day\b", re.I)
_SHORT_TERM = re.compile(r"\b(short[- ]term|a day or two|one or two days|a couple (of )?days|(two|2)[- ]day)\b", re.I)


def short_term_days(message: str) -> int | None:
    """The longest a record may last when the question asks only for short ones ("short-term permits (a
    day or two)"), else None. Code filters, not the model: given 53 records, it kept 1 of the 13 that
    lasted a day or two."""
    if _ONE_DAY.search(message):
        return 1
    return 2 if _SHORT_TERM.search(message) else None


def choose_detail(message: str, place_method: str) -> str:
    """brief (events, what changes getting there, a count of the rest) or full (every record).
    A trip question gets the brief; a question about permits, closures or one block gets every
    record; otherwise a neighborhood or a picked point gets the brief and a street, corner or
    address the full list, which is short there."""
    if _TRIP.search(message):
        return "brief"
    if _DETAIL.search(message):
        return "full"
    return "brief" if place_method in ("area", "click") else "full"


# Words that join places rather than name one: kept when a fragment is taken out of the message.
_JOINERS = {"and", "or", "at", "near", "by", "of", "on", "in", "the", "to", "from", "vs", "versus", "between"}


def _without(message: str, fragment: str) -> str:
    """The message with the fragment's place words taken out, to see whether it names another place."""
    words = {w for w in re.findall(r"[a-z0-9]+", fragment.lower()) if w not in _JOINERS}
    rest = re.sub(r"[A-Za-z0-9]+", lambda m: "" if m.group(0).lower() in words else m.group(0), message)
    return re.sub(r"\s+", " ", rest).strip()


# Fields every permit repeats that no answer uses; dropping them keeps a full list a fraction of the tokens.
_DROP = {"source_url", "source_authority"}


_PER_PERMIT = ("event_id", "start_date", "end_date", "start_ts", "end_ts", "open_ended")

# Each record's kind in the words people ask in, shown to the model in place of event_type: why the street is
# affected, the same three reasons the map's colors show. The data's "construction" is building work only, so
# asked "Is there construction in Francisville?" the model cited the three records labeled construction and
# left out six milling and paving closures labeled roadwork. To a person, road, utility and building work are
# all construction, and so is the odd other permit (a banner, an unmapped work type).
KIND = {
    "roadwork": "construction",
    "utility_work": "construction",
    "construction": "construction",
    "other": "construction",
    "block_party": "block party",
    "event": "event",
    "film_shoot": "event",
    "parking_restriction": "no parking (a parking or loading restriction, not a closure)",
    "reported_issue": "311 report",
}


def _plain_kind(e: dict) -> dict:
    if "event_type" not in e:
        return e
    t = e.pop("event_type")
    return e | {"kind": KIND.get(t, t)}


def _for_model(out: dict) -> dict:
    """A full record list as the model reads it: the same records without boilerplate fields, each
    one's kind in plain words (KIND) rather than its event_type, and
    permits that share a title, block and occupancy folded into one item listing each permit
    ({"count": 3, "permits": [...]}). Listed separately, the model kept merging them into one
    ("3 PGW permits on New Market St" answered as one)."""
    events = [_plain_kind({k: v for k, v in e.items()
                           if k not in _DROP and v is not None and v is not False
                           and not (k == "organization" and str(v).endswith("(permit issuer)"))})
              for e in out["events"]]
    key = lambda e: (e.get("source"), e.get("title"), e.get("description"), e.get("occupancy"))
    sizes = Counter(key(e) for e in events)
    items, groups = [], {}
    for e in events:
        if sizes[key(e)] == 1:
            items.append(e)
            continue
        if key(e) not in groups:
            groups[key(e)] = {k: v for k, v in e.items() if k not in _PER_PERMIT} | {"count": sizes[key(e)], "permits": []}
            items.append(groups[key(e)])
        groups[key(e)]["permits"].append({k: e[k] for k in _PER_PERMIT if k in e})
    return out | {"events": items}


def _past_names(digest: dict) -> dict:
    """past_events_here as names and years only. With a record per year to cite, the model often
    answered "permits this Saturday" with a table of past Marathons; the years' records come from
    get_location_history, which a history question calls anyway."""
    return {"events": [{"name": e.get("name"), "years": [y.get("year") for y in e.get("years", [])]}
                       for e in digest.get("events", [])],
            "note": "Past years' event closures at this place, for questions about past years or a recurring "
                    "event only: then call get_location_history for dates, streets and records to cite. "
                    "Otherwise ignore this."}


_IDS = {
    "place_id": {"type": "string", "description": "A place_id from resolve_place or the context (pin, prev_place)"},
    "window_id": {"type": "string", "description": "A window_id from resolve_window or the context "
                                                   "(prev_window, default_window)"},
}

SCHEMAS = [
    {"type": "function", "function": {
        "name": "resolve_place",
        "description": "Look up a place the user named (a corner like '13th and Locust', a street, an address or a "
                       "neighborhood) and get a place_id. Pass only the place words, not the whole question.",
        "parameters": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "resolve_window",
        "description": "Turn the user's time words ('next week', 'Saturday morning', 'November 22', 'March 2025') "
                       "into a window_id. Pass only the time words.",
        "parameters": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "search_events",
        "description": "Every permit, curated event and other item at the place and active in the window. Code "
                       "picks the answer shape from the question (searched.answer_shape): a brief for an area or "
                       "a trip, every record for permits, closures or one block. If truncated is true, more "
                       "matches exist: say the list is partial.",
        "parameters": {"type": "object", "required": ["place_id", "window_id"], "properties": {
            **_IDS, "include_311": {"type": "boolean"}}}}},
    {"type": "function", "function": {
        "name": "find_event",
        "description": "An event the user names ('Rocky Run', 'the Marathon', 'Christmas Village'), city-wide; no "
                       "place needed. Returns PENN's past editions from City closure notices (dates, hours, streets, "
                       "a cite per edition), the next date (announced, or estimated from past dates) and the latest "
                       "route; and web results when PENN has no record or no notice for this year yet. Pass only "
                       "the event's name.",
        "parameters": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "search_web",
        "description": "Search the web for a question about Philadelphia that PENN's records do not answer: a "
                       "kind of event ('food festivals', 'Halloween events'), anything city-wide, when something "
                       "usually happens, or anything the other tools found no record of. Pass a short query of "
                       "what and where ('East Passyunk food festival'), never the user's whole message or "
                       "personal details. Results are web pages, not City records.",
        "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "get_location_history",
        "description": "Past City-announced event closures at this place (races, parades, festivals, one-off "
                       "events), grouped by event and year, with times. Use it for questions about past years, "
                       "whether something happens every year, or what usually closes here.",
        "parameters": {"type": "object", "required": ["place_id", "window_id"], "properties": {
            **_IDS, "since_year": {"type": "integer", "description": "Earliest year to include (default 2016)"}}}}},
]

# The last step of a record answer when PENN_STRUCTURED=1 (penn.agent.render): the model says which records
# answer the question, code writes their facts. Not in SCHEMAS, so the free-text loop never offers it.
SUBMIT_ANSWER = {"type": "function", "function": {
    "name": "submit_answer",
    "description": "Finish the answer. List the event_ids that answer the question; code writes each record's "
                   "title, blocks, dates, counts, caveats and citation from the tool results. Use only event_ids "
                   "the tools returned this turn.",
    "parameters": {"type": "object", "required": ["direct_answer", "records", "no_record"], "properties": {
        "direct_answer": {"type": "string", "description": "One sentence answering the question (yes, no, or what "
                          "kind of thing), with no titles, blocks, dates, counts or citations. Never say a street "
                          "is closed."},
        "records": {"type": "array", "description": "Every record that answers the question, on every street and "
                    "block the search covered. Empty when none does.",
                    "items": {"type": "object", "required": ["event_id", "why"], "properties": {
                        "event_id": {"type": "string", "description": "An event_id copied from a tool result"},
                        "why": {"type": "string", "description": "A few words on why it answers the question"}}}},
        "no_record": {"type": "boolean", "description": "true when no record the tools returned answers it"},
        "checked": {"type": "string", "description": "With no_record: the kind of record looked for, e.g. "
                    "'a block party on Judson St'"}}}}}
