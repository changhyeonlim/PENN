"""One small function per check. All text checks run on normalize()d text, and the
facts and patterns in questions.jsonl are written in that same normalized form."""
from __future__ import annotations

import re
from functools import lru_cache

from .schema import Question, Result

_DASHES = re.compile(r"[‐‑‒–—―−]")
_SPACES = re.compile(r"[    \s]+")
_WORDS = {
    "street": "st", "streets": "sts", "avenue": "ave", "boulevard": "blvd", "parkway": "pkwy",
    "lane": "ln", "square": "sq", "south": "s", "north": "n",
    "january": "jan", "february": "feb", "march": "mar", "april": "apr", "june": "jun", "july": "jul",
    "august": "aug", "september": "sep", "sept": "sep", "october": "oct", "november": "nov", "december": "dec",
}
_WORD_RE = re.compile(r"\b(" + "|".join(_WORDS) + r")\b")


def normalize(text: str) -> str:
    t = text.lower()
    t = _DASHES.sub("-", t)
    t = re.sub(r"[*`_\"“”‘’']", "", t)   # markdown and quotes; "can't" -> "cant"
    t = re.sub(r"(?<=[a-z])\.", "", t)                         # "oct." -> "oct", "a.m." -> "am"
    t = re.sub(r"(?<=[a-z])-(?=[a-z])", " ", t)                # "permit-in-effect" -> "permit in effect"
    t = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", t)            # "13th" -> "13"
    t = _WORD_RE.sub(lambda m: _WORDS[m.group(1)], t)
    return _SPACES.sub(" ", t).strip()


@lru_cache(maxsize=None)
def _compile(alt: str) -> re.Pattern:
    """A fact alternative: "re:<regex>" as written, otherwise a whole-word phrase that may
    end in a plural "s"/"es" ("manhole" matches "manholes")."""
    if alt.startswith("re:"):
        return re.compile(alt[3:])
    phrase = normalize(alt)
    plural = "(?:s|es)?" if phrase[-1:].isalpha() else ""
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase) + plural + r"(?![a-z0-9])")


def group_present(group: list[str], norm_text: str) -> bool:
    return any(_compile(alt).search(norm_text) for alt in group)


# --- the checks -----------------------------------------------------------------------------

def tool_match(q: Question, r: Result) -> bool:
    """Every expected tool was called at least once. "a|b" means either one counts."""
    called = set(r.tools_called)
    return all(called & set(entry.split("|")) for entry in q.expected_tools)


def fact_coverage(q: Question, r: Result) -> tuple[float, list[int]]:
    """Share of fact groups present, and the indexes of the missing groups."""
    t = normalize(r.answer)
    missing = [i for i, g in enumerate(q.required_facts) if not group_present(g, t)]
    n = len(q.required_facts)
    return ((n - len(missing)) / n if n else 1.0), missing


def citation_valid(r: Result) -> float:
    """Share of cited IDs the tools actually returned. 1.0 when nothing is cited."""
    if not r.cited:
        return 1.0
    returned = set(r.returned_ids)
    return sum(c in returned for c in r.cited) / len(r.cited)


def id_recall(q: Question, r: Result) -> float | None:
    """Share of expected records the answer cites. None for questions that expect none."""
    if not q.expect_ids:
        return None
    cited = set(r.cited)
    return sum(i in cited for i in q.expect_ids) / len(q.expect_ids)


def retrieval_recall(q: Question, r: Result) -> float | None:
    """Share of expected records the TOOLS returned. Low here = a tool/data problem, not the model."""
    if not q.expect_ids:
        return None
    got = set(r.returned_ids)
    return sum(i in got for i in q.expect_ids) / len(q.expect_ids)


def shape_match(q: Question, r: Result) -> bool | None:
    """Every search used the answer shape the question expects (code picks brief or full from the
    message). None when the question does not say."""
    if not q.answer_shape:
        return None
    return bool(r.answer_shapes) and all(s == q.answer_shape for s in r.answer_shapes)


def forbidden_hits(q: Question, r: Result) -> list[str]:
    """Snippets of the answer that match a forbidden pattern (for a human to judge)."""
    t = normalize(r.answer)
    hits = []
    for pat in q.forbidden:
        m = re.search(pat, t)
        if m:
            hits.append(t[max(0, m.start() - 30): m.end() + 30])
    return hits


def score(q: Question, r: Result) -> Result:
    """Fill r.scores and r.passed. Pass = tools, every fact group, valid citations, nothing forbidden,
    and the expected answer shape when the question sets one."""
    facts, missing = fact_coverage(q, r)
    s = {
        "tool_match": tool_match(q, r),
        "fact_coverage": round(facts, 3),
        "missing_facts": missing,
        "citation_valid": round(citation_valid(r), 3),
        "id_recall": id_recall(q, r),
        "retrieval_recall": retrieval_recall(q, r),
        "forbidden_hits": forbidden_hits(q, r),
        "shape_match": shape_match(q, r),
    }
    r.scores = s
    r.passed = (r.error is None and s["tool_match"] and facts == 1.0
                and s["citation_valid"] == 1.0 and not s["forbidden_hits"] and s["shape_match"] is not False)
    return r
