"""The loop is tested with a fake client that replays scripted model replies,
so no API key or network is needed."""
import json
from datetime import date, datetime
from types import SimpleNamespace as NS

import pytest

from penn import tools
from penn.agent import loop
from penn.agent.context import Context
from penn.geo import locate
from penn.timeutil import Window
from penn.web import tavily

from .conftest import needs

CELLS = [617733123456789]
WIN = ("2026-10-10T06:00:00-04:00", "2026-10-10T12:00:00-04:00")
PERMIT = {"event_id": "lane_closures:2025-07887", "title": "Storm Water Installation", "occupancy": "Full Closure"}
SEARCH = {"place_id": "given_place", "window_id": "given_window"}


def tool_call(name, args, id_="call_1"):
    fn = NS(name=name, arguments=json.dumps(args))
    tc = NS(id=id_, type="function", function=fn)
    msg = NS(content=None, tool_calls=[tc], role="assistant")
    msg.model_dump = lambda **kw: {"role": "assistant", "content": None, "tool_calls": [
        {"id": id_, "type": "function", "function": {"name": name, "arguments": fn.arguments}}]}
    return msg


def text(content):
    msg = NS(content=content, tool_calls=None, role="assistant")
    msg.model_dump = lambda **kw: {"role": "assistant", "content": content}
    return msg


class FakeClient:
    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        msg = self.replies.pop(0)
        return NS(choices=[NS(message=msg)], usage=NS(prompt_tokens=10, completion_tokens=5))


@pytest.fixture
def fake_tools(monkeypatch):
    calls = []

    def search_events(cells, start, end, **kw):
        calls.append({"cells": cells, "start": start, "end": end, **kw})
        return {"events": [PERMIT], "count": 1}

    monkeypatch.setattr(tools, "search_events", search_events)
    return calls


def context():
    start = datetime.fromisoformat(WIN[0])
    ctx = Context(start, date(2016, 1, 1))
    ctx.add_place(locate.Resolved(cells=CELLS, label="Judson St", method="test"), {"text": "Judson St"}, "given_place")
    ctx.add_window(Window(start, datetime.fromisoformat(WIN[1]), "Saturday Oct 10 morning"), "given_window")
    return ctx


def run(client, q="What is happening on Judson St Saturday morning?", history=None, ctx=None):
    return needs(loop.ask, q, ctx or context(), history=history, client=client)


def test_runs_tool_then_answers_with_citation(fake_tools):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("A storm water permit is in effect [id:lane_closures:2025-07887]."),
    ])
    a = run(client)
    assert fake_tools and fake_tools[0]["cells"] == CELLS and fake_tools[0]["start"] == WIN[0]
    assert a.tools_called == ["search_events"]
    assert a.cited == ["lane_closures:2025-07887"]
    assert len(a.calls) == 2 and all("model" in c and "latency_ms" in c for c in a.calls)
    assert client.requests[0]["tools"], "pass the tool schemas as tools="
    second = client.requests[1]["messages"]
    assert any(m.get("role") == "tool" for m in second), "tool results go back as role='tool' messages"


def test_made_up_citation_gets_one_rewrite(fake_tools):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("Closed all day [id:lane_closures:FAKE]."),
        text("A permit is in effect [id:lane_closures:2025-07887]."),
    ])
    a = run(client)
    assert a.cited == ["lane_closures:2025-07887"]
    assert [c["kind"] for c in a.checks] == ["unknown_record"]       # the guard line says what was caught


def test_a_clean_answer_has_nothing_sent_back(fake_tools):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("A storm water permit is in effect [id:lane_closures:2025-07887]."),
    ])
    assert run(client).checks == []


def test_an_uncited_draft_is_named_as_uncited(fake_tools):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("A storm water permit is in effect on Judson St."),
        text("A storm water permit is in effect [id:lane_closures:2025-07887]."),
    ])
    a = run(client)
    assert a.checks == [{"kind": "uncited", "label": loop.SENT_BACK["uncited"]}]


def test_gives_up_honestly_after_second_bad_answer(fake_tools):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("Closed [id:lane_closures:FAKE]."),
        text("Still closed [id:lane_closures:FAKE2]."),
    ])
    a = run(client)
    assert a.text == loop.GIVE_UP and a.cited == []
    assert [c["kind"] for c in a.checks] == ["unknown_record", "unknown_record"]


def test_several_ids_in_one_bracket_come_back_one_each(fake_tools):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("Storm water permits are in effect 【ids:lane_closures:2025-07887, 2025-07887】."),
    ])
    a = run(client)
    assert a.cited == ["lane_closures:2025-07887"]
    assert "[id:lane_closures:2025-07887] [id:lane_closures:2025-07887]" in a.text and "【" not in a.text


def test_unreadable_cite_gets_one_rewrite_then_is_dropped(fake_tools):
    good = "A storm water permit is in effect [id:lane_closures:2025-07887]"
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text(f"{good}, see 【past_events_here】."),
        text(f"{good}, see 【past_events_here】."),
    ])
    a = run(client)
    assert "cannot read" in client.requests[-1]["messages"][-1]["content"]      # the first draft was sent back
    assert a.cited == ["lane_closures:2025-07887"] and "【" not in a.text         # the rewrite stands without it


def test_history_goes_in_without_old_citations(fake_tools):
    client = FakeClient([text("Hi! Ask me about a street.")])
    run(client, q="thanks", history=[("What is on Judson St?", "Note: x\nA permit [id:lane_closures:2025-07887].")])
    msgs = client.requests[0]["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert "[id:" not in msgs[2]["content"] and "Note:" not in msgs[2]["content"]
    assert "Context:" in msgs[3]["content"] and "given_place" in msgs[3]["content"]


def test_unknown_place_id_is_an_error_not_a_search(fake_tools):
    client = FakeClient([
        tool_call("search_events", {"place_id": "p9", "window_id": "given_window"}),
        text("Which street do you mean?"),
    ])
    a = run(client)
    tool_msg = next(m for m in client.requests[1]["messages"] if m.get("role") == "tool")
    assert "unknown place_id" in tool_msg["content"] and not fake_tools
    assert a.text == "Which street do you mean?"


def test_handle_cites_are_dropped_not_failed(fake_tools):
    """A right "no record" answer that cites a place_id/window_id is not failed for citing them."""
    no_record = "PENN has no record of a block party on Judson St [id:given_place:given_window]."
    client = FakeClient([tool_call("search_events", SEARCH), text(no_record), text(no_record)])
    a = run(client, q="Is there a block party on Judson St?")
    assert a.text == "PENN has no record of a block party on Judson St."
    assert "given_place" not in client.requests[-1]["messages"][-1]["content"]    # the rewrite asked for no unknown IDs


def test_think_tags_are_not_part_of_the_answer(fake_tools):
    """A reasoning model's <think> block (or only its closing tag) is dropped before the citation check."""
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("<think>Maybe cite [id:lane_closures:DRAFT]?</think>\nA permit is in effect [id:lane_closures:2025-07887]."),
    ])
    a = run(client)
    assert a.text.splitlines()[0] == "A permit is in effect [id:lane_closures:2025-07887]."
    assert "<think>" not in a.text and a.cited == ["lane_closures:2025-07887"]
    assert loop.strip_think("reasoning only</think> Answer.") == "Answer."
    assert loop.strip_think("Answer.<think>never closed") == "Answer."


def test_model_can_be_set_per_turn(fake_tools):
    client = FakeClient([text("Hi! Ask me about a street.")])
    a = needs(loop.ask, "hi", context(), client=client, model="org/other-model")
    assert client.requests[0]["model"] == "org/other-model" and a.route.model == "org/other-model"


WEB_PAGE = {"event_id": "web:0a1b2c3d", "title": "Flavors on the Avenue", "url": "https://visitphilly.com/epx",
            "site": "visitphilly.com", "excerpt": "Flavors on the Avenue, a food festival every spring.",
            "text": "Flavors on the Avenue, a food festival every spring on East Passyunk Avenue."}


@pytest.fixture
def fake_web(monkeypatch):
    queries = []

    def search_open(query, **kw):
        queries.append(query)
        return {"query": f"{query} Philadelphia", "results": [WEB_PAGE]}

    monkeypatch.setattr(tavily, "search_open", search_open)
    return queries


def test_open_question_goes_to_the_web(fake_tools, fake_web):
    client = FakeClient([
        tool_call("search_web", {"query": "East Passyunk food festival"}),
        text("Yes: Flavors on the Avenue, a spring food festival, according to visitphilly.com [id:web:0a1b2c3d]."),
    ])
    a = run(client, q="Does East Passyunk ever have a food festival?")
    assert fake_web == ["East Passyunk food festival"] and a.cited == ["web:0a1b2c3d"]


def test_no_record_is_sent_to_the_web_once(fake_tools, fake_web):
    """"No record" from City records alone is not an answer to an open question: the loop asks for search_web."""
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("PENN has no record of a food festival on Judson St."),
        tool_call("search_web", {"query": "Judson St food festival"}, "call_2"),
        text("PENN has no City record of one, but visitphilly.com lists Flavors on the Avenue [id:web:0a1b2c3d]."),
    ])
    a = run(client, q="Is there ever a food festival on Judson St?")
    contents = [m.get("content") for m in client.requests[-1]["messages"]]
    assert contents.count(loop.WEB_NUDGE) == 1
    assert contents.index(loop.WEB_NUDGE) == contents.index("PENN has no record of a food festival on Judson St.") + 1
    assert fake_web and a.cited == ["web:0a1b2c3d"]


def test_permit_no_record_stays_with_penn(fake_tools, fake_web):
    no_record = "PENN has no record of a plumbing permit on Judson St."
    client = FakeClient([tool_call("search_events", SEARCH), text(no_record), text(no_record)])
    a = run(client, q="Any plumbing permits on Judson St?")
    assert a.text == no_record and not fake_web


class ScriptedClient(FakeClient):
    """FakeClient whose replies are (message, finish_reason) pairs, or an exception to raise."""

    def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        msg, finish = reply
        return NS(choices=[NS(message=msg, finish_reason=finish)], usage=NS(prompt_tokens=10, completion_tokens=5))


def test_cut_off_answer_is_rewritten_not_accepted(fake_tools):
    client = ScriptedClient([
        (tool_call("search_events", SEARCH), "tool_calls"),
        (text("A storm water permit is in effect [id:lane_closures:2025-07887] and also"), "length"),
        (text("A storm water permit is in effect [id:lane_closures:2025-07887]."), "stop"),
    ])
    a = run(client)
    assert a.text.startswith("A storm water permit is in effect") and not a.text.endswith("and also")
    assert "cut off" in client.requests[-1]["messages"][-1]["content"]
    assert client.requests[0]["max_tokens"] and client.requests[0]["timeout"]


def test_timeout_gives_an_honest_answer(fake_tools):
    import httpx
    from openai import APITimeoutError
    client = ScriptedClient([APITimeoutError(request=httpx.Request("POST", "https://x"))])
    a = run(client)
    assert a.text == loop.TIMED_OUT and a.calls[0]["error"] == "timeout"


def test_permit_groups_must_be_counted_once():
    group = {"events": [{"title": "PGW Leak", "count": 3, "permits": [
        {"event_id": "lane_closures:A"}, {"event_id": "lane_closures:B"}, {"event_id": "lane_closures:C"}]}]}
    folded = loop.permit_groups(group)
    assert folded == [(3, {"lane_closures:A", "lane_closures:B", "lane_closures:C"}, "PGW Leak")]
    cited = {"lane_closures:A"}
    assert loop._uncounted("PGW Leak, permits Jul 10 to Feb 23 [id:lane_closures:A]", cited, folded)
    assert not loop._uncounted("Three PGW Leak permits are in effect [id:lane_closures:A]", cited, folded)
    assert not loop._uncounted("Something else [id:lane_closures:Z]", {"lane_closures:Z"}, folded)


def test_permit_line_is_added_to_full_answers_citing_permits():
    cite = "A permit is in effect Oct 6 to Dec 31 [id:lane_closures:2026-22421]."
    assert loop._add_permit_line(cite, ["full"]).endswith(loop.PERMIT_LINE)
    assert loop._add_permit_line(cite, ["brief"]) == cite
    already = cite + " Permits list dates only, not daily hours or which days work happens."
    assert loop._add_permit_line(already, ["full"]) == already
    assert loop._add_permit_line("Closure [id:event_closures:x].", ["full"]) == "Closure [id:event_closures:x]."


def test_answer_rules_come_with_the_first_result_of_their_kind(fake_tools):
    client = FakeClient([
        tool_call("search_events", SEARCH, "call_1"),
        tool_call("search_events", SEARCH, "call_2"),
        text("A storm water permit is in effect [id:lane_closures:2025-07887]."),
    ])
    run(client)
    msgs = client.requests[-1]["messages"]
    assert "ANSWER SHAPE" not in msgs[0]["content"], "shape rules ride with search results, not the system prompt"
    first, second = [json.loads(m["content"]) for m in msgs if m.get("role") == "tool"]
    assert first["how_to_answer"][0] == "HOW TO DESCRIBE A PERMIT"
    assert any(line.startswith("ANSWER SHAPE: FULL") for line in first["how_to_answer"])
    assert "how_to_answer" not in second, "each set of rules is sent once per turn"


def test_no_rules_for_lookups_or_errors():
    assert loop.rules_for("resolve_place", {"place_id": "p1"}) == []
    assert loop.rules_for("search_events", {"error": "unknown place_id"}) == []
    assert loop.rules_for("find_event", {"matches": ["A", "B"]}) == []
    assert loop.rules_for("search_events", {"searched": {"answer_shape": "brief"}}) == ["permits", "brief"]


def test_records_seen_reads_singles_and_groups():
    result = {"events": [
        {"event_id": "lane_closures:A", "title": "Loading/Unloading", "description": "600 block of N American St"},
        {"title": "PWD Excavation", "description": "500 block of Woodbrook Ln", "count": 2,
         "permits": [{"event_id": "lane_closures:B", "open_ended": True}, {"event_id": "lane_closures:C"}]}]}
    seen = loop.records_seen(result)
    assert seen["lane_closures:A"] == {"title": "Loading/Unloading", "where": "600 block of N American St", "open_ended": False}
    assert seen["lane_closures:B"]["open_ended"] and seen["lane_closures:C"]["title"] == "PWD Excavation"


def test_uncited_no_record_is_shown_the_records_before_giving_up(fake_tools):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("PENN has no record of storm water work on Judson St."),
        text("A Storm Water Installation permit listing a Full Closure is in effect [id:lane_closures:2025-07887]."),
    ])
    a = run(client, q="Any storm water permits on Judson St Saturday morning?")   # a permit question: no web nudge
    recheck = client.requests[2]["messages"][-1]["content"]
    assert "Storm Water Installation" in recheck and "[id:lane_closures:2025-07887]" in recheck
    assert "check each record" in recheck and a.cited == ["lane_closures:2025-07887"]


def test_open_ended_caveat_is_added_once():
    records = {"lane_closures:A": {"title": "PWD", "where": "", "open_ended": True},
               "lane_closures:B": {"title": "Valet", "where": "", "open_ended": False}}
    ans = "A PWD permit is in effect since May 27, 2024, no end date listed [id:lane_closures:A]."
    assert loop._add_open_line(ans, {"lane_closures:A"}, records, ["full"]).endswith(loop.OPEN_LINE)
    assert loop._add_open_line(ans, {"lane_closures:B"}, records, ["full"]) == ans
    hedged = ans + " PENN cannot confirm whether work is happening."
    assert loop._add_open_line(hedged, {"lane_closures:A"}, records, ["full"]) == hedged
    assert loop._add_open_line(ans, {"lane_closures:A"}, records, ["brief"]) == ans


def test_closed_claims_only_count_unhedged_facts():
    assert loop.closed_claims("Yes, Ludlow Street is closed on Saturday. Two permits list a Full Closure.", True, False)
    assert not loop.closed_claims("This shows a permit is in effect, not that the street is closed.", True, False)
    assert not loop.closed_claims("Broad St is closed from 7 AM, per the City notice.", False, False)   # an announcement
    assert loop.closed_claims("Streets near 22nd St will be closed Friday through Sunday.", False, True)
    assert not loop.closed_claims("Streets near 22nd St will be closed Friday through Sunday.", False, False)


def test_closed_claim_gets_one_rewrite(fake_tools):
    client = FakeClient([
        tool_call("search_events", SEARCH),
        text("Yes, Judson St is closed on Saturday [id:lane_closures:2025-07887]."),
        text("A permit listing a Full Closure is in effect on Judson St [id:lane_closures:2025-07887]."),
    ])
    a = run(client)
    assert "Rewrite without stating that a street is or will be closed" in client.requests[2]["messages"][-1]["content"]
    assert a.text.startswith("A permit listing a Full Closure is in effect")


def test_closed_claim_about_an_announcement_is_not_flagged():
    brief = ("A City advisory: 3rd St (Market to Poplar) is closed 10 AM to 2:15 PM [id:event_closures:adv-1].\n"
             "- Loading/Unloading, permit in effect Oct 17 only [id:lane_closures:A]")
    assert not loop.closed_claims(brief, True, False)
