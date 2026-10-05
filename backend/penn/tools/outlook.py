"""Disruption outlook: a count ratio with its base rate and sample size. No model.

Unit of comparison: one (H3 cell, past window) pair. A pair is a "hit" when that
cell had at least one street-related 311 report during that window. The target
rate uses the target cells; the base rate uses every Center City cell, so both
rates are per block, per window, and comparable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

MIN_WINDOWS = 20


@dataclass
class Outlook:
    target_hits: int
    target_pairs: int
    base_hits: int
    base_pairs: int
    past_windows: int
    scheduled_now: list[str]          # event_ids active in the window (facts, not predictions)
    recurring: dict[str, dict]        # series -> {editions, touched, years}

    @property
    def target_rate(self) -> float | None:
        return self.target_hits / self.target_pairs if self.target_pairs else None

    @property
    def base_rate(self) -> float | None:
        return self.base_hits / self.base_pairs if self.base_pairs else None

    @property
    def enough_history(self) -> bool:
        return self.past_windows >= MIN_WINDOWS

    @property
    def ratio(self) -> float | None:
        if not self.enough_history or not self.base_rate or self.target_rate is None:
            return None
        return self.target_rate / self.base_rate

    def summary(self) -> dict:
        d = asdict(self)
        d.update(
            target_rate=self.target_rate,
            base_rate=self.base_rate,
            enough_history=self.enough_history,
            ratio=self.ratio,
            note=self.note(),
        )
        return d

    def note(self) -> str:
        if not self.enough_history:
            return (
                f"Only {self.past_windows} comparable past windows; too few to compare "
                "against the base rate. Report the counts only."
            )
        r = self.ratio
        if r is None:
            return "No base rate available."
        if 0.8 <= r <= 1.25:
            return "About the same as a typical Center City block for this window."
        return "Higher than a typical block for this window." if r > 1 else "Lower than a typical block for this window."


SQL_HITS = """
SELECT count() AS hits FROM (
    SELECT DISTINCT cell, toDate(start_ts, 'America/New_York') AS d
    FROM penn.events FINAL
    ARRAY JOIN h3_cells AS cell
    WHERE source = 'philly_311'
      AND event_type IN %(street_types)s
      AND cell IN %(cells)s
      AND toDate(start_ts, 'America/New_York') IN %(dates)s
      AND toHour(start_ts, 'America/New_York') >= %(h0)s
      AND toHour(start_ts, 'America/New_York') < %(h1)s
)
"""
