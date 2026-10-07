"""Ask PENN: the tool-calling loop.  >>> YOURS TO BUILD <<<

See docs/LEARNING.md, steps 1 to 3 and 5. Tests that define "done":
tests/learning/test_loop.py (uses a fake client, so no API key is needed).

What is already decided for you:
- Location and time are resolved BEFORE this runs. You receive H3 cells and an
  ISO window; the model must not invent either.
- The tools exist: `TOOLS` maps a name to a Python function, `TOOL_SCHEMAS` is
  the list you pass to the API as `tools=`. Read both in penn/tools/__init__.py.
- `citations.collect_ids(result)` gathers IDs from a tool result, and
  `citations.check(text, ids)` tells you whether an answer is properly cited.
- Token Factory speaks the OpenAI chat-completions format, so the `openai`
  package works with `base_url=settings.token_factory_base_url`.

The loop you are building, in plain terms:
1. Send the system prompt and the question (plus the resolved context).
2. If the reply asks for tool calls, run each tool, append its result as a
   "tool" message, and ask again.
3. If the reply is text, run the citation check. Accept it, or ask for one
   rewrite, then give up with an honest message.
4. Record one entry per model call (model, reason, latency, tokens) in `calls`.
"""
from __future__ import annotations
from openai import OpenAI
from dataclasses import dataclass, field
import json
from penn.config import settings
from penn.tools import TOOL_SCHEMAS, TOOLS
from .router import Route, route_question, maybe_escalate
import time
from . import citations

SYSTEM = """You are PENN, a guide to street activity in Center City, Philadelphia.
Rules:
- Use only facts from tool results. Cite every fact as [id:<event_id>].
- A permit means "permit in effect", never "closed". Permits do not state daily hours.
- 311 records are resident reports, not confirmed events.
- For outlooks, always give the place's rate, the base rate, and the number of past windows.
  If enough_history is false, give counts only and say the sample is too small.
- Parking: you only know Temporary No Parking and Temporary Loading Zone permits.
  Say that regular sign rules are not in PENN's data.
- If the tools return nothing relevant, say PENN has no record of it.
The resolved location and time window are given to you; do not change them."""

MAX_STEPS = 6
GIVE_UP = "PENN could not produce an answer it can support with its records. Try a narrower question."


@dataclass
class Answer:
    text: str
    route: Route
    tools_called: list[str] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)
    request_id: str = ""
    calls: list[dict] = field(default_factory=list)


def ask(question: str, cells: list[int], start: str, end: str, place_label: str, window_label: str,
        client=None) -> Answer:
    """Run one Ask PENN turn and return a cited answer."""
    client = client or OpenAI(base_url=settings.token_factory_base_url, api_key=settings.token_factory_api_key)
    route = route_question(question)
    context = json.dumps({"cells": cells, "start": start, "end": end, "place": place_label, "window": window_label})
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"{question}\n\nResolved context: {context}"},
    ]
    def invoke(messages):
        resp = client.chat.completions.create(
            model=route.model,
            messages=messages,
            tools=TOOL_SCHEMAS
        )
        return resp
    tools_called, calls = [], []
    returned_ids = set()
    retried = False
    for _ in range(MAX_STEPS):
        t0 = time.monotonic()
        resp = invoke(messages)
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
                args = json.loads(tc.function.arguments)
                result = TOOLS[tc.function.name](**args)
                returned_ids |= citations.collect_ids(result)
                tools_called.append(tc.function.name)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str)
                })
            route = maybe_escalate(route, len(tools_called))
            continue
        chk = citations.check(msg.content or "", returned_ids)
        if chk.ok:
            return Answer(msg.content, route, tools_called, sorted(chk.cited), "", calls)
        if retried:
            break                                    # second bad answer: give up
        retried = True
        messages.append({"role": "assistant", "content": msg.content})
        messages.append({"role": "user",
                        "content": f"Citation check failed: {chk.reason}. Rewrite using only IDs the tools returned."})

    return Answer(GIVE_UP, route, tools_called, [], "", calls)