"""The common record every source adapter produces."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Literal

SourceAuthority = Literal["official", "curated", "reported", "derived"]
ClaimCertainty = Literal["scheduled", "observed", "inferred", "planned"]


@dataclass
class NormalizedEvent:
    source: str                      # e.g. "lane_closures", "philly_311", "recurring_events"
    source_record_id: str            # stable ID within the source (permit number, request ID)
    event_type: str                  # roadwork, utility, event, reported_issue, parking_restriction ...
    title: str
    source_url: str
    source_authority: SourceAuthority
    claim_certainty: ClaimCertainty
    geometry_wkt: str                # full shape for the map
    centroid_lat: float
    centroid_lon: float
    h3_r9: int                       # centroid cell
    h3_cells: list[int] = field(default_factory=list)  # every cell the shape touches
    description: str = ""
    organization: str = ""
    occupancy: str = ""              # Full Closure, Partial Closure, Footway (lane closures only)
    # Date-only sources fill start_date/end_date; timestamped sources fill start_ts/end_ts.
    start_date: date | None = None
    end_date: date | None = None
    start_ts: datetime | None = None  # UTC
    end_ts: datetime | None = None    # UTC
    open_ended: bool = False
    status: str = ""                 # source's own status word (Current, Future, Open, Closed ...)
    raw_status: str = ""

    @property
    def event_id(self) -> str:
        return f"{self.source}:{self.source_record_id}"

    def to_row(self) -> dict:
        row = asdict(self)
        row["event_id"] = self.event_id
        return row
