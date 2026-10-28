"""Named places PENN knows (p3-10): "the Art Museum", "LOVE Park", "Kelly Drive", "Union Transfer".

data/areas/landmarks.json was built once from City address points, street centerline corners and
corridors, and Ticketmaster venues; nothing is looked up at runtime. Two uses:
- a question naming one searches there (locate.from_text);
- an answer mentioning some, when the question named no place, frames the map on them (api /agent).

Matching is on whole phrases (names and aliases), longest first, so "Rittenhouse Square" wins over
"Rittenhouse" and "Marian Anderson Hall - Kimmel Center" is one place, not two.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parents[3] / "data" / "areas" / "landmarks.json"


@dataclass(frozen=True)
class Landmark:
    name: str
    kind: str
    point: tuple[float, float]                  # (lon, lat)
    bounds: tuple[tuple[float, float], tuple[float, float]]
    cells: tuple[int, ...]
    phrases: tuple[str, ...]


def _norm(text: str) -> str:
    """Lower case, words only, every possessive 's dropped. Phrases and text go through the same steps, so
    "Rittenhouse Square's" finds "Rittenhouse Square" and "Pat's Steaks" finds "Pat's King of Steaks"'s
    alias "pat's steaks"; an alias written without the apostrophe ("pats steaks") covers typing it so."""
    t = re.sub(r"['’]s\b", "", text.lower())
    return " " + re.sub(r"[^a-z0-9]+", " ", t.replace("'", "").replace("’", "")).strip() + " "


@lru_cache(maxsize=1)
def load(path: Path = PATH) -> tuple[Landmark, ...]:
    if not path.exists():
        return ()
    out = []
    for l in json.loads(path.read_text(encoding="utf-8")).get("landmarks", []):
        phrases = tuple(dict.fromkeys(p for p in (_norm(x) for x in [l["name"], *l.get("aliases", [])]) if p.strip()))
        out.append(Landmark(l["name"], l["kind"], tuple(l["point"]), tuple(map(tuple, l["bounds"])),
                            tuple(l["cells"]), phrases))
    return tuple(out)


def _matches(text: str, items: tuple[Landmark, ...]) -> list[tuple[int, int, Landmark]]:
    """(start, length, landmark) for non-overlapping phrase hits, longest phrase first."""
    t = _norm(text)
    taken: list[tuple[int, int]] = []
    hits = []
    for phrase, lm in sorted(((p, lm) for lm in items for p in lm.phrases), key=lambda x: -len(x[0])):
        start = 0
        while (i := t.find(phrase, start)) >= 0:
            span = (i + 1, i + len(phrase) - 1)          # the phrase without its padding spaces
            if not any(a < span[1] and span[0] < b for a, b in taken):
                taken.append(span)
                hits.append((span[0], len(phrase.strip()), lm))
            start = i + 1
    return hits


def find(text: str, items: tuple[Landmark, ...] | None = None) -> list[Landmark]:
    """Every landmark the text names, in order of first mention, each once."""
    items = load() if items is None else items
    out: list[Landmark] = []
    for _, _, lm in sorted(_matches(text, items), key=lambda h: h[0]):
        if lm not in out:
            out.append(lm)
    return out


def best(text: str, items: tuple[Landmark, ...] | None = None) -> tuple[Landmark, int] | None:
    """The landmark named by the longest phrase, and that phrase's length (to weigh against a
    neighborhood name in the same text)."""
    items = load() if items is None else items
    hits = _matches(text, items)
    if not hits:
        return None
    _, n, lm = max(hits, key=lambda h: h[1])
    return lm, n
