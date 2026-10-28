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

LANE_CLOSURES_URL = (
    "https://services.arcgis.com/fLeGjb7u4uXqeF9q/arcgis/rest/services/"
    "LaneClosure_Master/FeatureServer/0/query"
)
LANE_CLOSURES_PAGE = 2000  # the layer's maxRecordCount


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
    model_super: str = field(default_factory=lambda: os.getenv("MODEL_SUPER", "nvidia/nemotron-3-super-120b-a12b"))
    # The one model that runs every Ask PENN turn; empty means model_super. Over four eval runs Ultra passed
    # more (30.25 vs 25.0 of 51) but its p90 was 33.4s against Super's 7.3s (docs/eval-results.md, "Super or
    # Ultra"). Set MODEL_CHAT to compare.
    model_chat: str = field(default_factory=lambda: os.getenv("MODEL_CHAT", ""))
    # Per model call. Super's largest call on the eval was 1.5k output tokens and 6s; Ultra and Qwen3.5
    # reached the endpoint's own 8192 cap, so set PENN_MAX_TOKENS=8192 (and a longer timeout) to run those.
    model_max_tokens: int = field(default_factory=lambda: int(os.getenv("PENN_MAX_TOKENS", "4096")))
    model_timeout_s: float = field(default_factory=lambda: float(os.getenv("PENN_MODEL_TIMEOUT", "60")))

    ais_gatekeeper_key: str = field(default_factory=lambda: os.getenv("AIS_GATEKEEPER_KEY", ""))
    # Ticketmaster Discovery API consumer key (developer.ticketmaster.com). TICKETMASTER_API is the
    # name an earlier .env used.
    ticketmaster_api_key: str = field(default_factory=lambda: (
        os.getenv("TICKETMASTER_API_KEY") or os.getenv("TICKETMASTER_API") or "").strip())
    # Tavily web search, the fallback for events PENN has no record of (p5-09). TAVILY_API is the name
    # the .env already used.
    tavily_api_key: str = field(default_factory=lambda: (
        os.getenv("TAVILY_API_KEY") or os.getenv("TAVILY_API") or "").strip())

    # Demo mode: a fixed "now" (ISO 8601 with offset, e.g. 2026-10-17T09:00:00-04:00) so a frozen
    # snapshot (CLICKHOUSE_DB=penn_demo) answers the same way on any day.
    pinned_now: str = field(default_factory=lambda: os.getenv("PENN_NOW", ""))

    # Spend ceilings for one Ask PENN question, so no question can be made expensive (0 = no ceiling). The
    # public deployment sets them (deploy/endpoint/start.sh); a typical answer uses about 25k prompt tokens.
    turn_token_budget: int = field(default_factory=lambda: int(os.getenv("PENN_TURN_TOKEN_BUDGET", "0")))
    turn_seconds: float = field(default_factory=lambda: float(os.getenv("PENN_TURN_SECONDS", "0")))
    # Tavily searches that reach the network per day, per process (0 = no ceiling); cached searches are free.
    web_searches_per_day: int = field(default_factory=lambda: int(os.getenv("PENN_WEB_SEARCHES_PER_DAY", "0")))


settings = Settings()
