from __future__ import annotations

import logging
import os

from shared.tele_logging.formatter import format_error_alert
from shared.tele_logging.handler import AsyncTelegramHandler
from shared.tele_logging.sender import send_telegram_raw
from shared.tele_logging.throttler import ErrorThrottler

__all__ = [
    "AsyncTelegramHandler",
    "ErrorThrottler",
    "format_error_alert",
    "send_telegram_raw",
    "attach_telegram_error_handler",
]


def attach_telegram_error_handler(
    target_logger: logging.Logger | None = None,
    cooldown_seconds: float = 900.0,
) -> AsyncTelegramHandler | None:
    """Attaches AsyncTelegramHandler to the specified logger or root logger if enabled."""
    enabled = os.environ.get("TELEGRAM_ERROR_ALERTS_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        return None

    # Check if a bot token is available
    token = os.environ.get("TELEGRAM_ERROR_BOT_TOKEN") or os.environ.get("CONFIG_BOT_TOKEN")
    if not token:
        return None

    handler = AsyncTelegramHandler(cooldown_seconds=cooldown_seconds)
    logger = target_logger or logging.getLogger()
    logger.addHandler(handler)
    return handler
