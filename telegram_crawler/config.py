"""Configuration of the Telegram crawler (archive only)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
if (ROOT / ".env").exists():
    load_dotenv(ROOT / ".env")
if (ROOT.parent / ".env").exists():
    load_dotenv(ROOT.parent / ".env")



def _int(key: str, default: int = 0) -> int:
    raw = (os.getenv(key) or "").strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    raw = (os.getenv(key) or "").strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    raw = (os.getenv(key) or "").strip().lower()
    return default if not raw else raw in {"1", "true", "yes", "on"}


def database_dsn() -> str:
    url = os.getenv("TG_DATABASE_URL") or os.getenv("DATABASE_URL")
    if url:
        return url
    from psycopg.conninfo import make_conninfo

    params = {
        "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "dbname": os.getenv("POSTGRES_DB", "customer_yab"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
    }
    return make_conninfo(**{k: v for k, v in params.items() if v})


@dataclass(frozen=True)
class Settings:
    # Telegram credentials (Userbot)
    api_id: int = field(default_factory=lambda: _int("TG_API_ID"))
    api_hash: str = field(default_factory=lambda: os.getenv("TG_API_HASH", ""))
    session_path: str = field(
        default_factory=lambda: os.getenv(
            "TG_SESSION", str(ROOT / "data" / "telegram_crawler_session")
        )
    )

    # Database (PostgreSQL). TG_DATABASE_URL / DATABASE_URL, or the POSTGRES_* variables shared with Django.
    database_url: str = field(default_factory=lambda: database_dsn())
    db_schema: str = field(default_factory=lambda: os.getenv("TG_DB_SCHEMA", "crawler"))

    # What to store
    fetch_profiles: bool = field(default_factory=lambda: _bool("TG_FETCH_PROFILES", True))   # bio via GetFullUser, once per user
    profile_delay: float = field(default_factory=lambda: _float("TG_PROFILE_DELAY_SECONDS", 3.0))
    parent_depth: int = field(default_factory=lambda: _int("TG_PARENT_DEPTH", 3))             # reply chain levels to fetch
    store_raw: bool = field(default_factory=lambda: _bool("TG_STORE_RAW", True))
    # groups to watch come from the panel («جوامع آنلاین») table, polled every TG_PANEL_POLL_SECONDS
    panel_enabled: bool = field(default_factory=lambda: _bool("TG_PANEL_COMMUNITIES", True))
    panel_table: str = field(default_factory=lambda: os.getenv("TG_PANEL_TABLE", "public.discovery_monitoredcommunity"))
    panel_poll: float = field(default_factory=lambda: _float("TG_PANEL_POLL_SECONDS", 30.0))

    # Rate limiting
    flood_sleep_threshold: int = field(default_factory=lambda: _int("FLOOD_SLEEP_THRESHOLD", 60))
    flood_max_wait: int = field(default_factory=lambda: _int("FLOOD_MAX_WAIT_SECONDS", 900) or 900)
    flood_max_retries: int = field(default_factory=lambda: _int("FLOOD_MAX_RETRIES", 3) or 3)
    request_delay: float = field(default_factory=lambda: _float("REQUEST_DELAY_SECONDS", 0.3))

    # Crawling & Backfill defaults
    backfill_hours: float = field(
        default_factory=lambda: _float("BACKFILL_HOURS", 24.0)
    )
    backfill_limit: int = field(
        default_factory=lambda: _int("BACKFILL_LIMIT", 200) or 200
    )

    def require(self, *keys: str) -> None:
        """Ensure required keys are non-empty."""
        missing = [k for k in keys if not getattr(self, k)]
        if missing:
            raise ValueError(
                "Missing required configuration in .env: " + ", ".join(missing)
            )


settings = Settings()
