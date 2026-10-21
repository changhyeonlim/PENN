"""The structured answer (PENN_STRUCTURED=1, penn.agent.render): the model picks records with
submit_answer, code writes the text. Same fake client as test_loop.py, so no network."""
import json

import pytest

from penn import tools
from penn.agent import loop, render
from penn.config import Settings

from .test_loop import SEARCH, FakeClient, context, text, tool_call

PGW = {"source": "lane_closures", "event_type": "utility_work", "title": "PGW Leak",
       "description": "1000 block of NEW MARKET ST", "occupancy": "Partial Closure", "open_ended": False}
EVENTS = [
    PGW | {"event_id": "lane_closures:A", "start_date": "2026-07-14", "end_date": "2027-02-23"},
    PGW | {"event_id": "lane_closures:B", "start_date": "2026-07-16", "end_date": "2027-02-27"},
    PGW | {"event_id": "lane_closures:C", "start_date": "2026-07-17", "end_date": "2027-02-28"},
    {"event_id": "lane_closures:D", "source": "lane_closures", "event_type": "utility_work", "title": "PWD Excavation",
     "description": "200 block of S 12TH ST; 300 block of S 12TH ST", "occupancy": "Full Closure",
     "start_date": "2024-05-27", "end_date": None, "open_ended": True},
    {"event_id": "lane_closures:E", "source": "lane_closures", "event_type": "parking_restriction",
     "title": "Temporary Valet Zone", "description": "1300 block of LOCUST ST", "occupancy": "Footway",
     "start_date": "2026-10-10", "end_date": "2026-10-10", "open_ended": False},
    {"event_id": "event_closures:bike-ride", "source": "event_closures", "event_type": "event",
     "title": "Philly Bike Ride: Benjamin Franklin Parkway, 20th Street to N. 15th Street",
     "description": "Street closure announced by the City.", "organization": "City of Philadelphia", "occupancy": "",
     "start_date": "2026-10-10", "start_ts": "2026-10-10T07:00:00-04:00", "end_ts": "2026-10-10T12:00:00-04:00"},
]
Q = "Any permits on Judson St Saturday morning?"


@pytest.fixture
def records(monkeypatch):
    monkeypatch.setattr(tools, "search_events", lambda cells, start, end, **kw: {"events": EVENTS, "count": len(EVENTS)})


def submit(ids, direct="Yes, there are permits in effect there.", no_record=False, checked="", id_="call_9"):
    return tool_call("submit_answer", {"direct_answer": direct, "no_record": no_record, "checked": checked,
                                       "records": [{"event_id": i, "why": "on the searched blocks"} for i in ids]}, id_)


def run(client, q=Q):
    return loop.ask(q, context(), client=client, structured=True)


def tool_results(client):
    return [json.loads(m["content"]) for m in client.requests[-1]["messages"] if m.get("role") == "tool"]


def test_off_by_default_offers_no_submit_answer(records, monkeypatch):
    monkeypatch.delenv("PENN_STRUCTURED", raising=False)
    assert not Settings().structured
    client = FakeClient([text("Hi! Ask me about a street.")])
    loop.ask("hi", context(), client=client, structured=False)
    assert "submit_answer" not in [t["function"]["name"] for t in client.requests[0]["tools"]]
    assert render.SYSTEM not in client.requests[0]["messages"][0]["content"]


def test_on_offers_submit_answer_and_the_structured_rules(records):
    client = FakeClient([tool_call("search_events", SEARCH), submit(["lane_closures:E"])])
    run(client)
    assert "submit_answer" in [t["function"]["name"] for t in client.requests[0]["tools"]]
    assert "submit_answer" in client.requests[0]["messages"][0]["content"]
    rules = tool_results(client)[0]["how_to_answer"]
    assert "- Answer with submit_answer, not text. Code writes every record's facts and the counts." in rules


def test_records_are_written_by_code(records):
    client = FakeClient([tool_call("search_events", SEARCH), submit(["lane_closures:A", "lane_closures:D"])])
    a = run(client)
    lines = a.text.splitlines()
    assert lines[0] == "Yes, there are permits in effect there."
    assert lines[1] == "PENN found 4 permits for Judson St, Saturday Oct 10 morning:"
    # One cited member brings its whole same-title group, counted, each permit with its own cite.
    assert lines[2] == ("- 3 PGW Leak permits, 1000 block of New Market St, Partial Closure: permits in effect "
                        "Jul 14 to Feb 23, 2027 [id:lane_closures:A], Jul 16 to Feb 27, 2027 [id:lane_closures:B] "
                        "and Jul 17 to Feb 28, 2027 [id:lane_closures:C]")
    assert lines[3] == ("- PWD Excavation, 200 block of S 12th St and 300 block of S 12th St, Full Closure, "
                        "permit in effect from May 27, 2024, no end date listed [id:lane_closures:D]")
    assert render.OPEN_LINE in a.text and render.PERMIT_LINE in a.text and render.PARKING_LINE not in a.text
    assert a.cited == ["lane_closures:A", "lane_closures:B", "lane_closures:C", "lane_closures:D"]
    assert a.tools_called == ["search_events", "submit_answer"]
    assert render.OPEN_LINE == loop.OPEN_LINE and render.PERMIT_LINE == loop.PERMIT_LINE


def test_footway_announcement_and_parking(records):
    client = FakeClient([tool_call("search_events", SEARCH), submit(["lane_closures:E", "event_closures:bike-ride"])])
    a = run(client, q="Any parking permits or closures on Judson St Saturday morning?")
    assert "PENN found 1 permit and 1 announced closure for Judson St" in a.text
    assert ("- Temporary Valet Zone, 1300 block of Locust St, Footway (the sidewalk only, not a traffic lane), "
            "permit in effect Oct 10 only [id:lane_closures:E]") in a.text
    assert ("- Philly Bike Ride: Benjamin Franklin Parkway, 20th Street to N. 15th Street, Sat Oct 10, "
            "7:00 AM – 12:00 PM [id:event_closures:bike-ride]") in a.text
    assert a.text.endswith(render.PARKING_LINE) and render.OPEN_LINE not in a.text


def test_a_cited_id_the_tools_did_not_return_is_sent_back(records):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        submit(["lane_closures:FAKE"]),
        submit(["lane_closures:E"], id_="call_10"),
    ])
    a = run(client)
    err = tool_results(client)[-1]["error"]
    assert "did not return" in err and "lane_closures:FAKE" in err
    assert a.cited == ["lane_closures:E"] and "FAKE" not in a.text


def test_a_second_made_up_id_is_dropped_or_gives_up(records):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        submit(["lane_closures:FAKE"]),
        submit(["lane_closures:FAKE", "lane_closures:E"], id_="call_10"),
    ])
    assert run(client).cited == ["lane_closures:E"]
    client = FakeClient([tool_call("search_events", SEARCH), submit(["lane_closures:X"]), submit(["lane_closures:Y"])])
    a = run(client)
    assert a.text == loop.GIVE_UP and a.cited == []


def test_no_record_is_shown_the_records_once_then_written_by_code(records):
    q = "Is there a block party on Judson St Saturday morning?"   # a permit question: no web nudge
    no = lambda id_: submit([], direct="No.", no_record=True, checked="a block party on Judson St", id_=id_)
    client = FakeClient([tool_call("search_events", SEARCH), no("call_9"), no("call_10")])
    a = run(client, q=q)
    recheck = tool_results(client)[-1]["error"]
    assert "check each record" in recheck and "PGW Leak" in recheck and "(lane_closures:A)" in recheck
    assert a.text == ("No.\nPENN has no record of a block party on Judson St.\n"
                      "PENN searched Judson St, Saturday Oct 10 morning.")
    assert a.cited == []


def test_no_record_on_an_open_question_tries_the_web_first(monkeypatch):
    monkeypatch.setattr(tools, "search_events", lambda cells, start, end, **kw: {"events": [], "count": 0})
    no = lambda id_: submit([], direct="", no_record=True, checked="PENN has no record of a food festival.", id_=id_)
    client = FakeClient([tool_call("search_events", SEARCH), no("call_9"), no("call_10")])
    a = run(client, q="Is there ever a food festival on Judson St?")
    assert "call search_web" in tool_results(client)[-1]["error"]
    assert a.text.startswith("PENN has no record of a food festival.")


def test_direct_answer_states_no_closure_and_no_cites(records):
    client = FakeClient([tool_call("search_events", SEARCH),
                         submit(["lane_closures:E"], direct="Yes, Judson St is closed Saturday [id:lane_closures:E].")])
    a = run(client)
    assert a.text.startswith("PENN found 1 permit"), "a direct answer saying the street is closed is dropped"
    client = FakeClient([tool_call("search_events", SEARCH),
                         submit(["lane_closures:E"], direct="Yes, a valet permit [id:lane_closures:E].")])
    assert run(client).text.startswith("Yes, a valet permit.\nPENN found 1 permit")


def test_a_text_answer_to_a_full_search_is_asked_for_submit_answer_once(records):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("A valet permit is in effect [id:lane_closures:E]."),
        submit(["lane_closures:E"]),
    ])
    a = run(client)
    contents = [m.get("content") for m in client.requests[-1]["messages"]]
    assert contents.count(render.SUBMIT_NUDGE) == 1
    assert contents.index(render.SUBMIT_NUDGE) == contents.index("A valet permit is in effect [id:lane_closures:E].") + 1
    assert a.cited == ["lane_closures:E"] and a.text.startswith("Yes, there are permits")


def test_records_without_a_title_go_back_to_text(monkeypatch):
    """find_event / get_location_history editions carry an event_id but no title: answered in text."""
    monkeypatch.setattr(tools, "search_events", lambda cells, start, end, **kw: {
        "events": [], "count": 0, "past": [{"year": 2025, "event_id": "event_closures:marathon-2025"}]})
    client = FakeClient([
        tool_call("search_events", SEARCH),
        submit(["event_closures:marathon-2025"]),
        text("The Marathon closed streets here in 2025 [id:event_closures:marathon-2025]."),
    ])
    a = run(client, q="Did the Marathon close streets on Judson St?")
    assert "Answer in text instead" in tool_results(client)[-1]["error"]
    assert a.cited == ["event_closures:marathon-2025"]


def test_web_pages_are_quoted_not_paraphrased():
    sub = render.Structured("food festival?", context().now)
    sub.see({"results": [{"event_id": "web:0a1b2c3d", "title": "Flavors on the Avenue", "site": "visitphilly.com",
                          "excerpt": "Flavors on the Avenue, a food festival every spring."}]})
    out = sub.submit({"direct_answer": "Yes.", "records": [{"event_id": "web:0a1b2c3d", "why": "festival"}],
                      "no_record": False}, {"web:0a1b2c3d"}, True)
    assert out.text == ("Yes.\nPENN found 1 web page:\n- Flavors on the Avenue, according to visitphilly.com: "
                        "“Flavors on the Avenue, a food festival every spring.” [id:web:0a1b2c3d]")


def test_long_block_lists_are_cut():
    desc = "; ".join(f"{n} block of RHAWN ST" for n in range(100, 1400, 100))
    assert render._blocks(desc).endswith("800 block of Rhawn St and 5 more blocks")
