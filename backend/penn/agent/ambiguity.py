"""Ambiguity notes: fixed sentences for questions PENN knows are ambiguous, added in code.

Prompt rules alone never got the model to flag ambiguity (0 of 3 eval questions across three
runs), so like routing this is plain code: detect the known cases, return one sentence each,
and the API puts them above the answer. The sentences are fixed, so they are testable and the
model cannot drop them.

Known cases:
- "construction": road work, utility work or building work (sidewalk sheds, cranes).
- "closed" / "closure": PENN has permits and announcements that list closures; it cannot see
  whether a street is actually closed.
- a whole street named ("on Spruce Street") when the search covered one corner or area.
"""
from __future__ import annotations

import re

CONSTRUCTION = re.compile(r"\bconstruction\b", re.I)
CLOSED = re.compile(r"\b(closed|closures?|shut)\b", re.I)
STREET_SUFFIX = r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Parkway|Pkwy|Road|Rd|Lane|Ln)"
WHOLE_STREET = re.compile(rf"\b(?:on|along|down|up)\s+((?:[A-Z][a-z]+|\d+(?:st|nd|rd|th))(?:\s+[A-Z][a-z]+)?\s+{STREET_SUFFIX})\b")


def _core(name: str) -> str:
    """ "Spruce Street" -> "spruce"; "S 13th St" -> "13th"."""
    words = [w for w in re.sub(r"[^\w\s]", " ", name.lower()).split() if w not in {"n", "s", "e", "w"}]
    return words[0] if words else ""


def notes(question: str, place: str = "") -> list[str]:
    out = []
    if CONSTRUCTION.search(question):
        out.append("\"Construction\" can mean road work, utility work or building work (sidewalk sheds, "
                   "cranes); PENN lists permits of all three kinds.")
    if CLOSED.search(question):
        out.append("\"Closed\" can mean that a permit or a City announcement lists a closure, or that traffic "
                   "is actually stopped; PENN only has the permits and announcements, so it cannot confirm "
                   "that a street is actually closed at a given moment.")
    for m in WHOLE_STREET.finditer(question):
        street = m.group(1)
        if place and _core(street) not in place.lower():
            out.append(f"{street} is longer than the area PENN searched; this answer covers only the "
                       f"part near {place}.")
            break
    return out


def with_notes(answer: str, question: str, place: str = "") -> str:
    ns = notes(question, place)
    return ("\n".join(f"Note: {n}" for n in ns) + "\n\n" + answer) if ns else answer
