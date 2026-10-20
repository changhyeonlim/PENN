"""Runtime settings, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
LOCAL_TZ = ZoneInfo("America/New_York")

# H3 resolution 9 cells are roughly 0.1 km2: about one city block group.
H3_RES = 9

# Approximate Center City bounding box (Vine St to South St, Schuylkill to Delaware).
# Used only to limit 311 backfill volume. Replace with the neighborhood polygon
# chosen in recon for anything user-facing.
CENTER_CITY_BBOX = {"min_lat": 39.940, "max_lat": 39.962, "min_lon": -75.186, "max_lon": -75.138}

LANE_CLOSURES_URL = (
    "https://services.arcgis.com/fLeGjb7u4uXqeF9q/arcgis/rest/services/"
    "LaneClosure_Master/FeatureServer/0/query"
)
LANE_CLOSURES_PAGE = 2000  # the layer's maxRecordCount
CARTO_SQL_URL = "https://phl.carto.com/api/v2/sql"


@dataclass(frozen=True)
class Settings:
    clickhouse_host: str = field(default_factory=lambda: os.getenv("CLICKHOUSE_HOST", "localhost"))
    clickhouse_port: int = field(default_factory=lambda: int(os.getenv("CLICKHOUSE_PORT", "8123")))
    clickhouse_user: str = field(default_factory=lambda: os.getenv("CLICKHOUSE_USER", "default"))
    clickhouse_password: str = field(default_factory=lambda: os.getenv("CLICKHOUSE_PASSWORD", ""))
    clickhouse_db: str = field(default_factory=lambda: os.getenv("CLICKHOUSE_DB", "penn"))
    clickhouse_secure: bool = field(default_factory=lambda: os.getenv("CLICKHOUSE_SECURE", "0") == "1")

    token_factory_base_url: str = field(
        default_factory=lambda: os.getenv(
            "TOKEN_FACTORY_BASE_URL", "https://api.tokenfactory.us-central1.nebius.com/v1/"
        )
    )
    token_factory_api_key: str = field(default_factory=lambda: os.getenv("TOKEN_FACTORY_API_KEY", ""))
    # Confirm exact IDs in the Token Factory console before the demo.
    model_nano: str = field(default_factory=lambda: os.getenv("MODEL_NANO", "nvidia/nemotron-3-nano-30b-a3b"))
    model_super: str = field(default_factory=lambda: os.getenv("MODEL_SUPER", "nvidia/nemotron-3-super-120b-a12b"))
    model_ultra: str = field(default_factory=lambda: os.getenv("MODEL_ULTRA", "nvidia/nemotron-3-ultra-550b-a55b"))

    ais_gatekeeper_key: str = field(default_factory=lambda: os.getenv("AIS_GATEKEEPER_KEY", ""))
    # Ticketmaster Discovery API consumer key (developer.ticketmaster.com). TICKETMASTER_API is the
    # name an earlier .env used.
    ticketmaster_api_key: str = field(default_factory=lambda: (
        os.getenv("TICKETMASTER_API_KEY") or os.getenv("TICKETMASTER_API") or "").strip())

    # Demo mode: a fixed "now" (ISO 8601 with offset, e.g. 2026-10-17T09:00:00-04:00) so a frozen
    # snapshot (jobs/freeze_demo.py, CLICKHOUSE_DB=penn_demo) answers the same way on any day.
    pinned_now: str = field(default_factory=lambda: os.getenv("PENN_NOW", ""))


settings = Settings()
