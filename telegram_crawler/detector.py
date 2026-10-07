"""Target message detection logic (pluggable filter function)."""

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


# Global default detector instance
default_detector = KeywordTargetDetector()


async def is_target_message(text: str, metadata: dict[str, Any] | None = None) -> bool:
    """
    Main evaluation entry point.
    Replace or override this function in the future to call an LLM API or custom classifier.
    """
    return await default_detector(text, metadata)
