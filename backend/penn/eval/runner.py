"""Run each question through the real agent loop and capture the full trace.

The loop takes an OpenAI-style client, so the runner hands it a RecordingClient: it
forwards every call to Token Factory and keeps what the loop does not return itself,
namely the IDs the tools returned (read from the "tool" messages) and how many text
drafts the model wrote (a second draft means the citation check forced a rewrite).
"""
from __future__ import annotations

import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from openai import OpenAI

from datetime import date, datetime

from ..agent import ambiguity, citations
from ..agent.context import Context
from ..agent.loop import GIVE_UP, ask
from ..config import settings
from ..geo import locate
from ..timeutil import Window
from .schema import Question, Result


class RecordingClient:
    """Looks like OpenAI() to the loop: client.chat.completions.create(**kw)."""

    def __init__(self, real: OpenAI):
        self._real = real
        self.returned_ids: set[str] = set()
        self.tool_args: list[dict] = []     # every tool call the model asked for, with its arguments
        self.draft_texts: list[str] = []    # every text answer, including ones the citation check rejected
        self.answer_shapes: list[str] = []  # brief/full per search, as code chose it (searched.answer_shape)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        shapes = []     # the messages hold every tool result so far, so rebuild the list each call
        for m in kw.get("messages", []):
            if isinstance(m, dict) and m.get("role") == "tool":
                try:
                    result = json.loads(m["content"])
                except (ValueError, TypeError):
                    continue
                self.returned_ids |= citations.collect_ids(result)
                if isinstance(result, dict) and (shape := (result.get("searched") or {}).get("answer_shape")):
                    shapes.append(shape)
        self.answer_shapes = shapes or self.answer_shapes
        resp = self._real.chat.completions.create(**kw)
        msg = resp.choices[0].message
        if msg.tool_calls:
            for tc in msg.tool_calls:
                args = {k: v for k, v in _loads(tc.function.arguments).items() if k != "cells"}
                self.tool_args.append({"name": tc.function.name, "args": args})
        else:
            self.draft_texts.append(msg.content or "")
        return resp


def _loads(s: str) -> dict:
    try:
        return json.loads(s or "{}")
    except ValueError:
        return {"_unparsed": s}


def seeded_context(q: Question) -> Context:
    """The question's own place and window, already looked up, so the eval scores answering (as it
    always has) and not place lookup. The model still may call resolve_place, and the score shows it."""
    start = datetime.fromisoformat(q.start)
    now = datetime.fromisoformat(q.now) if q.now else start     # default: the questions were written as current
    ctx = Context(now, date(2016, 1, 1), message=q.question)
    loc = locate.Resolved(cells=q.cells, label=q.place, method=q.place_method, seg_ids=q.seg_ids)
    # The same IDs the API's own up-front lookup uses (Context.add_message), with the eval's exact cells.
    ctx.add_place(loc, {"text": q.place}, "this_place", note="the place in this message, already looked up")
    ctx.add_window(Window(start, datetime.fromisoformat(q.end), q.window), "this_window",
                   note="the time in this message, already looked up")
    return ctx


def run_question(q: Question, real: OpenAI, model: str | None = None) -> Result:
    rec = RecordingClient(real)
    r = Result(id=q.id, model=model or "")
    t0 = time.monotonic()
    try:
        a = ask(q.question, seeded_context(q), client=rec, model=model)
        # Same pipeline as the API: code-level ambiguity notes go above the model's answer.
        r.answer = ambiguity.with_notes(a.text, q.question, q.place)
        r.model, r.route_reason = a.route.model, a.route.reason
        r.tools_called, r.cited, r.calls = a.tools_called, list(a.cited), a.calls
        r.gave_up = a.text == GIVE_UP
    except Exception as e:  # one broken question must not stop the run
        r.error = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
    r.latency_ms = int((time.monotonic() - t0) * 1000)
    r.returned_ids = sorted(rec.returned_ids)
    r.drafts = len(rec.draft_texts)
    r.draft_texts = rec.draft_texts
    r.tool_args = rec.tool_args
    r.answer_shapes = rec.answer_shapes
    r.prompt_tokens = sum(c.get("prompt_tokens") or 0 for c in r.calls)
    r.output_tokens = sum(c.get("output_tokens") or 0 for c in r.calls)
    return r


def run_all(questions: list[Question], workers: int = 4, on_done=None, model: str | None = None) -> list[Result]:
    """Run questions in parallel; results come back in question order. `model` overrides
    settings.model_chat (None = the model the API uses)."""
    real = OpenAI(base_url=settings.token_factory_base_url, api_key=settings.token_factory_api_key, max_retries=1)

    def one(q: Question) -> Result:
        r = run_question(q, real, model)
        if on_done:
            on_done(q, r)
        return r

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(one, questions))
