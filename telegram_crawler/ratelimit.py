"""Telegram flood-wait handling and request pacing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, TypeVar

from telethon.errors import FloodWaitError

from telegram_crawler.config import settings

log = logging.getLogger("telegram_crawler.ratelimit")
T = TypeVar("T")


class FloodWaitTooLong(RuntimeError):
    """Telegram asked us to wait longer than FLOOD_MAX_WAIT_SECONDS."""


async def with_flood_retry(
    factory: Callable[[], Awaitable[T]],
    *,
    what: str = "telegram request",
    max_retries: int | None = None,
    max_wait: int | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> T:
    """Run ``factory()``; on FloodWaitError sleep the requested time and retry.

    ``factory`` must create a *new* awaitable on every call (e.g. a lambda).
    """
    retries = settings.flood_max_retries if max_retries is None else max_retries
    limit = settings.flood_max_wait if max_wait is None else max_wait
    attempt = 0
    while True:
        try:
            return await factory()
        except FloodWaitError as exc:
            attempt += 1
            wait = int(getattr(exc, "seconds", 0) or 0) + 1
            if wait > limit:
                raise FloodWaitTooLong(f"{what}: Telegram requires waiting {wait}s (> {limit}s limit)") from exc
            if attempt > retries:
                raise
            log.warning("FloodWait on %s: sleeping %ss (attempt %s/%s)", what, wait, attempt, retries)
            await sleep(wait)


async def pace() -> None:
    """Small fixed delay between consecutive Telegram API calls."""
    if settings.request_delay > 0:
        await asyncio.sleep(settings.request_delay)
