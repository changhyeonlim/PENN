"""Model routing: decide which Nemotron model handles a turn.  >>> YOURS TO BUILD <<<

See docs/LEARNING.md, step 4. Tests that define "done": tests/learning/test_router.py

The rule in the spec:
- Nano  : bulk classification (311 service types). Never used for chat turns.
- Super : the default for an Ask PENN turn that needs one or two tool calls.
- Ultra : questions about history, comparison or explanation ("why", "before",
          "has this happened"), OR a Super turn that has already needed more than
          two tool calls.

Routing is plain code, not a model. Every decision returns a human-readable
`reason`, because the demo shows the routing log.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import settings  # settings.model_nano / model_super / model_ultra
import re
ULTRA_PATTERNS = [
    re.compile(r"\bwhy\b", re.IGNORECASE),
    re.compile(r"\bbefore\b", re.IGNORECASE),
    re.compile(r"\bhas (this|that|it) happened\b", re.IGNORECASE),
    re.compile(r"\bcompar(e|ed|ison)\b", re.IGNORECASE),
    re.compile(r"\busually\b", re.IGNORECASE),
    re.compile(r"\bthan (last|usual)\b", re.IGNORECASE)
]
MAX_TOOLS_BEFORE_ESCALATE = 2


@dataclass(frozen=True)
class Route:
    model: str
    tier: str      # "nano" | "super" | "ultra"
    reason: str


def route_question(question: str) -> Route:
    """Pick the starting model for a user question.

    Hint: start with a short list of regex patterns that signal history,
    comparison or explanation. Return Ultra with a reason naming the pattern,
    otherwise Super.
    """
    for pattern in ULTRA_PATTERNS:
        m = pattern.search(question)
        if m:
            return Route(settings.model_ultra, "ultra", f"Found keyword <{m.group(0)}> and changing to Ultra")
    return(Route(settings.model_super, tier="super", reason="No keywords matching for Ultra"))

def maybe_escalate(current: Route, tool_calls_so_far: int) -> Route:
    """Move a Super turn to Ultra once it has needed more than two tool calls."""
    if tool_calls_so_far > MAX_TOOLS_BEFORE_ESCALATE and current.tier == 'super':
        return Route(settings.model_ultra, "ultra", f"Tool calling exceeded: # of tool calls [{tool_calls_so_far}]")
    else:
        return current


def classification_route() -> Route:
    """The route for bulk classification jobs (Nano)."""
    return Route(settings.model_nano, tier="nano", reason="classification job")
