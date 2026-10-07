"""Configuration management for Telegram Lead Crawler."""

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


def _keywords(key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(key, "")
    if not raw.strip():
        return default
    return tuple(k.strip() for k in raw.split(",") if k.strip())


DEFAULT_KEYWORDS = (
    "روغن موتور",
    "خریدارم",
    "خریدار هستم",
    "دنبال",
    "میخوام بخرم",
    "می خوام بخرم",
    "قصد خرید",
    "نیاز دارم",
    "لازم دارم",
    "قیمت چنده",
    "سراغ دارید",
    "کسی سراغ داره",
    "کسی داره",
    "خرید",
    "فروشنده",
)


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

    # Database
    db_path: str = field(
        default_factory=lambda: os.getenv("DB_PATH", str(ROOT / "data" / "leads.db"))
    )

    # Django Webhook Integration (Microservice)
    django_webhook_url: str = field(
        default_factory=lambda: os.getenv(
            "DJANGO_WEBHOOK_URL", "http://localhost:8000/discovery/api/leads/submit/"
        )
    )
    django_webhook_enabled: bool = field(
        default_factory=lambda: os.getenv("DJANGO_WEBHOOK_ENABLED", "true").strip().lower() == "true"
    )

    # Crawling & Backfill defaults
    backfill_hours: float = field(
        default_factory=lambda: _float("BACKFILL_HOURS", 24.0)
    )
    backfill_limit: int = field(
        default_factory=lambda: _int("BACKFILL_LIMIT", 200) or 200
    )
    context_msg_count: int = field(
        default_factory=lambda: _int("CONTEXT_MSG_COUNT", 5) or 5
    )

    # Keywords for default detector
    keywords: tuple[str, ...] = field(
        default_factory=lambda: _keywords("TARGET_KEYWORDS", DEFAULT_KEYWORDS)
    )

    def require(self, *keys: str) -> None:
        """Ensure required keys are non-empty."""
        missing = [k for k in keys if not getattr(self, k)]
        if missing:
            raise ValueError(
                "Missing required configuration in .env: " + ", ".join(missing)
            )


settings = Settings()
