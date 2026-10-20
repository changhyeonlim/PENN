"""Handles between the model and code: places and windows the model refers to by ID."""
from datetime import date, datetime

import pytest

from penn import tools
from penn.agent import context as ctxmod
from penn.agent.context import Context
from penn.geo import locate

NOW = datetime.fromisoformat("2026-10-17T10:30:00-04:00")    # a Saturday


@pytest.fixture
def offline(monkeypatch):
    """Stub the place lookup and the search, so the tests need no network or database."""
    places = {"Rittenhouse": locate.Resolved(cells=[1, 2, 3], label="Rittenhouse", method="area"),
              "13th and Locust": locate.Resolved(cells=[4], label="S 13th St & Locust St", method="intersection",
                                                 seg_ids=[77])}
    monkeypatch.setattr(ctxmod.locate, "from_text",
                        lambda t: places.get(t, locate.Resolved(method="none", needs_pin=True, label=t)))
    monkeypatch.setattr(ctxmod.locate, "from_click",
                        lambda lon, lat: locate.Resolved(cells=[9], label=f"{lat}, {lon}", method="click"))
    searched = []

    def search_events(cells, start, end, **kw):
        searched.append({"cells": cells, "start": start, "legs": tools.LEGS.get(), **kw})
        return {"events": [], "count": 0}

    monkeypatch.setattr(tools, "search_events", search_events)
    return searched


def make():
    return Context(NOW, date(2016, 1, 1))


def test_default_window_label_has_right_weekdays():
    w = make().windows["default_window"]
    assert w.label == "the next 7 days (Sat Oct 17 – Sat Oct 24)" and w.kind == "current"


def test_resolve_then_search_uses_cells_and_legs(offline):
    ctx = make()
    p = ctx.resolve_place("13th and Locust")
    w = ctx.resolve_window("next week")
    assert p["place_id"] == "p1" and w["window_id"] == "w1" and "cells" not in p
    out = ctx.call("search_events", {"place_id": "p1", "window_id": "w1"})
    # a corner, no trip words: every record (a corner's list is short)
    assert offline[0]["cells"] == [4] and offline[0]["legs"] == [77] and offline[0]["detail"] == "full"
    assert "types" not in offline[0] and out["searched"]["answer_shape"] == "full"
    assert tools.LEGS.get() is None                       # reset after the search
    assert out["searched"]["place"] == "S 13th St & Locust St"
    assert ctx.last_place().id == "p1" and ctx.last_window().id == "w1"


def test_unknown_handles_and_bad_calls_are_errors(offline):
    ctx = make()
    assert "unknown place_id" in ctx.call("search_events", {"place_id": "p7", "window_id": "default_window"})["error"]
    assert "bad arguments" in ctx.call("search_events", {"where": "here"})["error"]
    assert "no tool" in ctx.call("drop_table", {})["error"]
    assert not offline and not ctx.searches


def test_place_not_found_is_remembered_and_says_not_to_reuse(offline):
    ctx = make()
    r = ctx.resolve_place("the Parkway near the thing")
    assert r["found"] is False and "Do not use prev_place" in r["next"]
    assert ctx.unresolved == ["the Parkway near the thing"]


def test_previous_place_and_window_come_back(offline):
    ctx = make()
    ctx.add_previous({"place": {"text": "Rittenhouse", "label": "Rittenhouse"},
                      "window": {"start": "2026-10-19T00:00:00-04:00", "end": "2026-10-26T00:00:00-04:00",
                                 "label": "next week"}})
    assert ctx.places["prev_place"].loc.cells == [1, 2, 3]
    assert ctx.windows["prev_window"].kind == "future"
    ctx.call("search_events", {"place_id": "prev_place", "window_id": "prev_window"})
    assert ctx.used("prev_place") and ctx.used("prev_window")


def test_pin_and_before_records(offline):
    ctx = make()
    ctx.add_pin(-75.17, 39.96)
    assert ctx.places["pin"].query == {"lat": 39.96, "lon": -75.17}
    r = ctx.resolve_window("in 2010")
    assert r["kind"] == "before_records" and "no records" in r["answer"]
    assert "answer" in ctx.call("search_events", {"place_id": "pin", "window_id": r["window_id"]})
    assert not offline


def test_reuse_note_and_next_context(offline):
    from penn.api import _next_context, _reuse_note
    ctx = make()
    ctx.add_previous({"place": {"text": "Rittenhouse"}})
    ctx.call("search_events", {"place_id": "prev_place", "window_id": "default_window"})
    place, win = ctx.last_place(), ctx.last_window()
    assert _reuse_note(ctx, place, win) == "Using Rittenhouse from your last question."
    nxt = _next_context(place, win, None)
    assert nxt["place"] == {"text": "Rittenhouse", "label": "Rittenhouse"} and nxt["window"]["label"].startswith("the next 7")
    # a turn that searched nothing keeps the previous context
    assert _next_context(None, None, {"place": {"text": "Rittenhouse"}}) == {"place": {"text": "Rittenhouse"}}


def test_whole_message_is_looked_up_once_and_fragments_fall_back(offline, monkeypatch):
    ctx = make()
    monkeypatch.setattr(ctxmod.locate, "from_text",
                        lambda t: locate.Resolved(cells=[4], label="S 13th St & Locust St", method="intersection")
                        if "13th and Locust" in t else locate.Resolved(method="none", needs_pin=True, label=t))
    ctx.add_message("What permits are at 13th and Locust next week?")
    assert ctx.places["this_place"].label == "S 13th St & Locust St" and "this_window" in ctx.windows
    r = ctx.resolve_place("Locust Street near 13th")             # a fragment that finds nothing alone
    assert r["place_id"] == "this_place" and not ctx.unresolved
    other = make()
    other.add_message("what about next week?")                   # no place words: nothing to pre-fill
    assert "this_place" not in other.places and "this_window" in other.windows


def _two_places(monkeypatch):
    """Ludlow Street is matched as the Ludlow neighborhood; Rittenhouse and Old City are areas."""
    places = {"ludlow": locate.Resolved(cells=[50], label="Ludlow", method="area"),
              "rittenhouse": locate.Resolved(cells=[1, 2, 3], label="Rittenhouse", method="area"),
              "old city": locate.Resolved(cells=[7, 8], label="Old City", method="area")}

    def from_text(t):
        hit = next((v for k, v in places.items() if k in t.lower()), None)
        return hit or locate.Resolved(method="none", needs_pin=True, label=t)
    monkeypatch.setattr(ctxmod.locate, "from_text", from_text)


def test_a_fragment_of_this_message_searches_this_place(offline, monkeypatch):
    _two_places(monkeypatch)
    ctx = make()
    ctx.message = "Is Ludlow Street closed between 18th and 19th on Saturday?"
    ctx.add_place(locate.Resolved(cells=[1, 2], label="18th & Walnut St", method="eval"), {"text": "x"}, "this_place",
                  note="the place in this message, already looked up")
    # found elsewhere, but nothing else in the message names a place: it is this message's place
    assert ctx.resolve_place("Ludlow Street between 18th and 19th")["place_id"] == "this_place"
    assert ctx.resolve_place("Rittenhouse")["place_id"] == "this_place"          # overlaps this_place
    assert "p1" not in ctx.places


def test_a_second_place_in_the_message_gets_its_own_id(offline, monkeypatch):
    _two_places(monkeypatch)
    ctx = make()
    ctx.add_message("Is Rittenhouse or Old City busier this weekend?")
    assert ctx.places["this_place"].label == "Rittenhouse"
    r = ctx.resolve_place("Old City")
    assert r["place_id"] == "p1" and r["label"] == "Old City"


def test_no_this_place_still_asks(offline):
    ctx = make()
    ctx.add_message("is the big plaza by the art place closed Sunday?")
    assert "this_place" not in ctx.places
    r = ctx.resolve_place("the big plaza by the art place")
    assert r["found"] is False and ctx.unresolved == ["the big plaza by the art place"]


def test_event_name_window_points_to_history(offline):
    ctx = make()
    ctx.add_message("Has 20th St been closed for the Thanksgiving Day Parade in past years?")
    r = ctx.resolve_window("Thanksgiving Day Parade")
    assert r["found"] is False and "get_location_history" in r["next"] and "default_window" in r["next"]
    ctx.add_window(ctx.windows["default_window"].window, "this_window")
    assert "this_window" in ctx.resolve_window("Thanksgiving Day Parade")["next"]


def test_full_results_drop_boilerplate_and_fold_shared_titles(offline, monkeypatch):
    gas = {"source": "lane_closures", "title": "Pgw Leak", "description": "2100 block of ARCH ST",
           "source_url": "https://x", "source_authority": "official",
           "organization": "Philadelphia Streets Department (permit issuer)",
           "start_ts": None, "open_ended": False, "occupancy": "Partial Closure"}
    rows = [gas | {"event_id": f"lane_closures:EUN{i}", "start_date": f"2026-09-0{i + 1}"} for i in range(3)]
    rows.append(gas | {"event_id": "lane_closures:other", "description": "2000 block of ARCH ST"})
    monkeypatch.setattr(tools, "search_events", lambda cells, start, end, **kw: {"events": rows, "count": 4})
    ctx = make()
    ctx.add_previous({"place": {"text": "Rittenhouse"}})
    ctx.message = "What permits are on Arch St?"
    out = ctx.search_events("prev_place", "default_window")
    folded, single = out["events"]
    assert folded["count"] == 3 and folded["title"] == "Pgw Leak" and "event_id" not in folded
    assert folded["permits"][2] == {"event_id": "lane_closures:EUN2", "start_date": "2026-09-03"}
    assert single == {"source": "lane_closures", "title": "Pgw Leak", "description": "2000 block of ARCH ST",
                      "occupancy": "Partial Closure", "event_id": "lane_closures:other"}
    ctx.message = "what about next week?"                                        # an area, no detail words
    brief = ctx.search_events("prev_place", "default_window")                     # brief: as returned
    assert len(brief["events"]) == 4


def test_full_results_name_every_kind_of_construction(offline, monkeypatch):
    """Francisville, Oct 18 2026: asked about construction, the model cited only the records whose event_type
    was "construction" and left out the milling and paving closures (roadwork). The model now reads the
    reason, as the map colors it: road, utility and building work are all construction."""
    def rec(i, event_type, title):
        return {"event_id": f"lane_closures:F{i}", "source": "lane_closures", "event_type": event_type,
                "title": title, "description": f"{i}00 block of POPLAR ST", "occupancy": "Full Closure",
                "start_date": "2026-07-21", "end_date": "2026-11-30"}
    rows = [rec(1, "roadwork", "Milling And Prep Work For Bid 3002"),
            rec(2, "utility_work", "Pgw Leak"),
            rec(3, "construction", "Put Dumpster Only For Construction"),
            rec(4, "block_party", "Block Party")]
    monkeypatch.setattr(tools, "search_events", lambda cells, start, end, **kw: {"events": rows, "count": 4})
    ctx = make()
    ctx.add_previous({"place": {"text": "Rittenhouse"}})
    ctx.message = "Is there construction in Francisville?"
    events = ctx.search_events("prev_place", "default_window")["events"]
    assert all("event_type" not in e for e in events)
    kinds = {e["event_id"]: e["kind"] for e in events}
    assert [i for i, k in kinds.items() if k == "construction"] == [
        "lane_closures:F1", "lane_closures:F2", "lane_closures:F3"]
    assert kinds["lane_closures:F4"] == "block party"


def test_calls_forgive_made_up_arguments_and_missing_ids(offline):
    ctx = make()
    assert "bad arguments" in ctx.call("search_events", {"query": "race"})["error"]   # nothing to fall back on
    ctx.add_place(locate.Resolved(cells=[4], label="S 13th St & Locust St", method="intersection"), {"text": "x"},
                  "this_place")
    ctx.add_window(ctx.windows["default_window"].window, "this_window")
    assert "error" not in ctx.call("search_events", {"query": "race"})
    assert "error" not in ctx.call("search_events", {"place_id": "this_place"})
    assert ctx.searches == [("this_place", "this_window")] * 2


def test_past_events_here_keeps_names_and_years_only(offline, monkeypatch):
    digest = {"events": [{"name": "Mummers Parade", "recurrence": "annual",
                          "years": [{"year": 2024, "date": "2024-01-01", "event_id": "event_closures:m-2024"}]}],
              "note": "cite its event_id"}
    monkeypatch.setattr(tools, "search_events",
                        lambda cells, start, end, **kw: {"events": [], "count": 0, "past_events_here": digest})
    ctx = make()
    ctx.add_previous({"place": {"text": "Rittenhouse"}})
    past = ctx.search_events("prev_place", "default_window")["past_events_here"]
    assert past["events"] == [{"name": "Mummers Parade", "years": [2024]}] and "get_location_history" in past["note"]


def test_code_picks_brief_or_full_from_the_message():
    from penn.agent.context import choose_detail
    assert choose_detail("Rittenhouse next week", "area") == "brief"
    assert choose_detail("what about this weekend?", "area") == "brief"
    assert choose_detail("Can I drive to Rittenhouse Saturday, any closures?", "area") == "brief"   # a trip
    assert choose_detail("what is going on near here", "click") == "brief"
    assert choose_detail("What permits are at 13th and Locust this Saturday?", "intersection") == "full"
    assert choose_detail("Can I park near Rittenhouse Saturday?", "area") == "full"
    assert choose_detail("Is Manning Street closed to traffic behind 1810 Rittenhouse Square?", "area") == "full"
    assert choose_detail("Is it busy at 13th and Locust?", "intersection") == "full"            # a corner
    assert choose_detail("Anything in Old City on October 17, 2026 and after?", "area") == "brief"  # a date, not an address


def test_the_models_type_filter_is_ignored(offline):
    ctx = make()
    ctx.add_previous({"place": {"text": "Rittenhouse"}})
    assert "error" not in ctx.call("search_events", {"place_id": "prev_place", "window_id": "default_window",
                                                     "types": ["construction"], "detail": "full"})
    assert "types" not in offline[0] and offline[0]["detail"] == "brief"           # code's choice, not the model's


def test_short_term_questions_filter_in_code(offline):
    assert ctxmod.short_term_days("what short-term permits (a day or two) are around 18th and Walnut?") == 2
    assert ctxmod.short_term_days("any one-day loading zones on Locust?") == 1
    assert ctxmod.short_term_days("what permits are on Locust St?") is None
    ctx = make()
    ctx.message = "Between Monday and Friday, what short-term permits (a day or two) are scheduled here?"
    ctx.add_place(locate.Resolved(cells=[1], label="18th & Walnut St", method="eval"), {"text": "x"}, "this_place")
    out = ctx.search_events("this_place", "default_window")
    assert offline[0]["max_days"] == 2 and out["searched"]["max_days"] == 2 and "every one" in out["searched"]["note"]
    ctx.message = "what permits are on Walnut St?"
    assert "max_days" not in ctx.search_events("this_place", "default_window")["searched"]


def test_find_event_says_whether_this_place_was_on_the_route(monkeypatch):
    seen = {}
    monkeypatch.setattr(tools.history, "catalog", lambda: {"broad-street-run": {"name": "Broad Street Run"}})
    monkeypatch.setattr(ctxmod.event_lookup, "series_history",
                        lambda s, today: {"name": "Broad Street Run", "editions": [], "next": {"announced": "2027-05-02"}})

    def history(cells, start, end, since_year=2016):
        seen.update(cells=cells, end=end, legs=tools.LEGS.get())
        return {"events": [{"series": "broad-street-run", "editions": [
            {"year": 2026, "date": "2026-05-03", "cite": "[id:event_closures:bsr-snyder]"}]}]}

    monkeypatch.setattr(tools, "get_location_history", history)
    ctx = Context(datetime.fromisoformat("2026-05-03T00:00:00-04:00"), date(2016, 1, 1))   # race day, midnight
    ctx.message = "Was Broad Street closed at Snyder Avenue for the Broad Street Run this year?"
    ctx.add_place(locate.Resolved(cells=[5], label="Broad St & Snyder Ave", method="intersection", seg_ids=[9]),
                  {"text": "x"}, "this_place")
    at = ctx.find_event("Broad Street Run")["at_this_place"]
    assert at["on_route"] is True and at["editions"][0]["cite"] == "[id:event_closures:bsr-snyder]"
    assert seen["cells"] == [5] and seen["legs"] == [9] and seen["end"] > "2026-05-03T23"   # race day itself included
    monkeypatch.setattr(tools, "get_location_history", lambda *a, **k: {"events": []})
    assert ctx.find_event("Broad Street Run")["at_this_place"]["on_route"] is False
