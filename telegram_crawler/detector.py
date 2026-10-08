"""Candidate filter for the crawler (pluggable).

Since milestone 1 the crawler does NOT decide buying intent. The default
``BasicMessageFilter`` only drops messages that can never be useful (empty, very
short, bots). Everything else goes to the shared analysis agent, which handles
implicit needs that keyword matching cannot catch. ``KeywordTargetDetector`` is
kept as the legacy mode (CRAWLER_FILTER_MODE=keyword).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Awaitable
from telegram_crawler.config import settings

# Signature of detector functions: async func(text, metadata) -> bool
DetectorCallable = Callable[[str, dict[str, Any] | None], Awaitable[bool]]


def normalize_text(text: str) -> str:
    """Normalize Arabic/Persian characters for accurate keyword matching."""
    if not text:
        return ""
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = text.replace("\u200c", " ")  # Replace half-spaces with space for matching
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


class KeywordTargetDetector:
    """Default rule-based detector matching configured search keywords."""

    def __init__(self, keywords: tuple[str, ...] | None = None) -> None:
        raw_keywords = keywords if keywords is not None else settings.keywords
        self.keywords = [normalize_text(kw) for kw in raw_keywords if kw.strip()]
        self._patterns: list[re.Pattern] = []
        for kw in self.keywords:
            if " " in kw:
                self._patterns.append(re.compile(re.escape(kw)))
            else:
                self._patterns.append(
                    re.compile(rf"(?:^|[^\wآ-ی]){re.escape(kw)}(?:[^\wآ-ی]|$)")
                )

    async def __call__(self, text: str, metadata: dict[str, Any] | None = None) -> bool:
        return await self.check(text, metadata)

    async def check(self, text: str, metadata: dict[str, Any] | None = None) -> bool:
        if not text or len(text.strip()) < 5:
            return False
        norm = normalize_text(text)
        return any(p.search(norm) is not None for p in self._patterns)


_LETTERS = re.compile(r"[A-Za-z\u0600-\u06FF]")


class BasicMessageFilter:
    """Drop only messages that can never be leads: empty, too short, or sent by bots."""

    def __init__(self, min_chars: int | None = None) -> None:
        self.min_chars = settings.min_text_chars if min_chars is None else min_chars

    async def __call__(self, text: str, metadata: dict[str, Any] | None = None) -> bool:
        if not text:
            return False
        if metadata and metadata.get("is_bot"):
            return False
        return len(_LETTERS.findall(text)) >= self.min_chars


def build_default_detector() -> DetectorCallable:
    if settings.filter_mode == "keyword":
        return KeywordTargetDetector()
    return BasicMessageFilter()


# Global default detector instance
default_detector = build_default_detector()


async def is_target_message(text: str, metadata: dict[str, Any] | None = None) -> bool:
    """Main entry point used by the monitor (see module docstring)."""
    return await default_detector(text, metadata)
