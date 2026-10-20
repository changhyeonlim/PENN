"""Eval data shapes: one Question in, one Result out."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path

# backend/penn/eval/schema.py -> parents[3] is the repo root
EVAL_DIR = Path(__file__).resolve().parents[3] / "eval"


@dataclass
class Question:
    id: str
    question: str
    type: str                          # lookup, specific, negative, trap, multi_day, street_history
    cells: list[int]                   # resolved location (H3 r9)
    start: str                         # resolved window, ISO with offset
    end: str
    place: str                         # labels passed to ask()
    window: str
    expect_ids: list[str]              # records a correct answer cites
    required_facts: list[list[str]]    # each group passes if ANY alternative appears ("re:" = regex)
    forbidden: list[str]               # regex patterns that must NOT appear
    expected_tools: list[str]
    trap: str | None = None
    must_say: list[str] = field(default_factory=list)   # the human-readable version of the checks
    hold: str | None = None   # why it is outside the headline score (still run and reported)
    seg_ids: list[int] = field(default_factory=list)   # exact legs when the place is an intersection
    answer_shape: str | None = None   # "brief" or "full": the shape code must pick for this question
    now: str | None = None            # the asking time, when it is not the window start ("next week")
    place_method: str = "eval"        # how the place was found: "area" or "click" get the brief by default
    difficulty: str | None = None     # easy | medium | hard, from past pass rates; None = not rated yet

    @classmethod
    def from_dict(cls, d: dict) -> "Question":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def load_questions(path: Path = EVAL_DIR / "questions.jsonl") -> list[Question]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Question.from_dict(json.loads(line)) for line in lines if line.strip()]


@dataclass
class Result:
    """Everything one agent turn did, captured for scoring and for the results file."""
    id: str
    answer: str = ""
    model: str = ""
    route_reason: str = ""
    tools_called: list[str] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)          # IDs the final answer cites
    returned_ids: list[str] = field(default_factory=list)   # IDs the tools actually returned
    drafts: int = 0                                         # text answers written; >1 means a rewrite
    draft_texts: list[str] = field(default_factory=list)    # every draft, rejected ones included
    tool_args: list[dict] = field(default_factory=list)     # [{"name", "args"}] per tool call (cells omitted)
    answer_shapes: list[str] = field(default_factory=list)  # searched.answer_shape of each search, in order
    gave_up: bool = False
    calls: list[dict] = field(default_factory=list)         # one entry per model call (from the loop)
    latency_ms: int = 0
    prompt_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None
    scores: dict = field(default_factory=dict)
    passed: bool = False


def load_results(path: Path) -> list[Result]:
    """The results of a saved run (eval/results/run-*.json), ignoring fields this version does not know."""
    known = {f.name for f in fields(Result)}
    rows = json.loads(path.read_text(encoding="utf-8"))["results"]
    return [Result(**{k: v for k, v in r.items() if k in known}) for r in rows]
