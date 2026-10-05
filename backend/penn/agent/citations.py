"""Every fact in an answer must cite a record ID the tools actually returned.

The system prompt asks the model to cite as [id:<event_id>]. This check rejects
answers that cite unknown IDs, and answers that contain no citation while the
tools returned records.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CITE = re.compile(r"\[id:([A-Za-z0-9_:\-\.]+)\]")


def collect_ids(obj) -> set[str]:
    """Walk a tool result and gather every event_id it contains."""
    ids: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "event_id" and isinstance(v, str):
                ids.add(v)
            elif k in ("scheduled_now",) and isinstance(v, list):
                ids.update(x for x in v if isinstance(x, str))
            elif k == "by_occupancy" and isinstance(v, dict):
                for lst in v.values():
                    ids.update(lst)
            else:
                ids |= collect_ids(v)
    elif isinstance(obj, list):
        for x in obj:
            ids |= collect_ids(x)
    return ids


@dataclass
class CitationCheck:
    ok: bool
    cited: set[str] = field(default_factory=set)
    unknown: set[str] = field(default_factory=set)
    reason: str = ""


def check(answer: str, returned_ids: set[str]) -> CitationCheck:
    cited = set(CITE.findall(answer))
    unknown = cited - returned_ids
    if unknown:
        return CitationCheck(False, cited, unknown, f"cites IDs the tools did not return: {sorted(unknown)}")
    if returned_ids and not cited:
        return CitationCheck(False, cited, set(), "tools returned records but the answer cites none")
    return CitationCheck(True, cited, set(), "")
