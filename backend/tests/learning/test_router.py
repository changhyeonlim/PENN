import dataclasses

import pytest

from penn.agent import router
from penn.agent.router import Route, classification_route, maybe_escalate, route_question
from penn.config import settings

from .conftest import needs


@pytest.fixture(autouse=True)
def routing_on(monkeypatch):
    """These tests describe routing when it is on (PENN_ROUTING=1); it is paused by default."""
    monkeypatch.setattr(router, "settings", dataclasses.replace(settings, routing_enabled=True))


def test_paused_routing_always_uses_super(monkeypatch):
    monkeypatch.setattr(router, "settings", dataclasses.replace(settings, routing_enabled=False))
    assert route_question("Why do you think that?").tier == "super"
    start = Route(settings.model_super, "super", "x")
    assert maybe_escalate(start, 5).tier == "super"


def test_plain_lookup_goes_to_super():
    r = needs(route_question, "What permits are on Walnut St tomorrow?")
    assert r.tier == "super" and r.model == settings.model_super and r.reason


def test_why_question_goes_to_ultra():
    r = needs(route_question, "Why do you think that?")
    assert r.tier == "ultra" and r.model == settings.model_ultra


def test_history_question_goes_to_ultra():
    assert needs(route_question, "Has this happened here before?").tier == "ultra"


def test_super_escalates_after_two_tool_calls():
    start = Route(settings.model_super, "super", "single lookup question")
    assert needs(maybe_escalate, start, 2).tier == "super"
    assert needs(maybe_escalate, start, 3).tier == "ultra"


def test_classification_uses_nano():
    assert needs(classification_route).model == settings.model_nano
