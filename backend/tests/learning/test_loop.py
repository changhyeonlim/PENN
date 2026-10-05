"""The loop is tested with a fake client that replays scripted model replies,
so no API key or network is needed."""
import json
from types import SimpleNamespace as NS

import pytest

from penn import tools
from penn.agent import loop

from .conftest import needs

CELLS = [617733123456789]
WIN = ("2026-10-10T06:00:00-04:00", "2026-10-10T12:00:00-04:00")
PERMIT = {"event_id": "lane_closures:2025-07887", "title": "Storm Water Installation", "occupancy": "Full Closure"}


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

    def search_events(**kw):
        calls.append(kw)
        return {"events": [PERMIT], "count": 1}

    monkeypatch.setitem(tools.TOOLS, "search_events", search_events)
    return calls


def run(client, q="What is happening on Judson St Saturday morning?"):
    return needs(loop.ask, q, CELLS, *WIN, "Judson St", "Saturday Oct 10 morning", client=client)


def test_runs_tool_then_answers_with_citation(fake_tools):
    client = FakeClient([
        tool_call("search_events", {"cells": CELLS, "start": WIN[0], "end": WIN[1]}),
        text("A storm water permit is in effect [id:lane_closures:2025-07887]."),
    ])
    a = run(client)
    assert fake_tools and fake_tools[0]["cells"] == CELLS
    assert a.tools_called == ["search_events"]
    assert a.cited == ["lane_closures:2025-07887"]
    assert len(a.calls) == 2 and all("model" in c and "latency_ms" in c for c in a.calls)
    assert client.requests[0]["tools"], "pass TOOL_SCHEMAS as tools="
    second = client.requests[1]["messages"]
    assert any(m.get("role") == "tool" for m in second), "tool results go back as role='tool' messages"


def test_made_up_citation_gets_one_rewrite(fake_tools):
    client = FakeClient([
        tool_call("search_events", {"cells": CELLS, "start": WIN[0], "end": WIN[1]}),
        text("Closed all day [id:lane_closures:FAKE]."),
        text("A permit is in effect [id:lane_closures:2025-07887]."),
    ])
    a = run(client)
    assert a.cited == ["lane_closures:2025-07887"]


def test_gives_up_honestly_after_second_bad_answer(fake_tools):
    client = FakeClient([
        tool_call("search_events", {"cells": CELLS, "start": WIN[0], "end": WIN[1]}),
        text("Closed [id:lane_closures:FAKE]."),
        text("Still closed [id:lane_closures:FAKE2]."),
    ])
    a = run(client)
    assert a.text == loop.GIVE_UP and a.cited == []
