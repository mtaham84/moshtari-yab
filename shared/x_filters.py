"""Text filters shared by X collection and analysis."""
from __future__ import annotations

import re

PROMOTIONAL_PATTERNS = (
    r"\b(?:buy now|shop now|limited offer|order now|use code|discount|promo code|sponsored|giveaway)\b",
    r"(?:تخفیف|فروش ویژه|ثبت سفارش|سفارش دهید|خرید کنید|همین حالا بخرید|ارسال رایگان|کد تخفیف|فروش فوری|موجود شد|برای خرید|دایرکت بدهید|دایرکت دهید|تماس بگیرید|قیمت ویژه)",
)
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


def is_promotional_post(text: str) -> bool:
    lowered = text.casefold()
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in PROMOTIONAL_PATTERNS):
        return True
    return bool(URL_RE.search(lowered) and re.search(r"خرید|سفارش|فروش|order|buy|shop|دایرکت|تماس", lowered, re.I))


def is_political_x_post(text: str) -> bool:
    """Semantic embedding check to filter out political, protest, or governmental news posts
    from X (Twitter) BEFORE calling the LLM, saving 100% of LLM costs on political rants.
    """
    if not text or not text.strip():
        return False
    try:
        from apps.discovery.semantic_moderation import is_political_post_semantic
        is_pol, _, _ = is_political_post_semantic(text)
        return is_pol
    except Exception:
        return False

