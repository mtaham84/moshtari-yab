"""Conservative, inexpensive filters for independent public X posts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from need_engine.schemas import ChatMessage
from need_engine.text import BM25, norm

PROMOTIONAL_PATTERNS = (
    r"\b(?:buy now|shop now|limited offer|order now|use code|discount|promo code|sponsored|giveaway)\b",
    r"(?:تخفیف|فروش ویژه|ثبت سفارش|سفارش دهید|خرید کنید|همین حالا بخرید|ارسال رایگان|کد تخفیف|فروش فوری|موجود شد|برای خرید|دایرکت بدهید|دایرکت دهید|تماس بگیرید|قیمت ویژه)",
)
INTENT_TERMS = ("دنبال", "میخوام", "سراغ", "پیشنهاد", "معرفی کنید", "کجا بخرم", "چی بخرم", "توصیه", "راهنمایی", "نیاز دارم", "لازم دارم", "می خواهم", "قصد خرید")
SHOP_TERMS = ("فروشگاه", "ارسال به سراسر", "سفارش", "shop", "store", "official", "فروش")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)


def is_promotional_post(text: str) -> bool:
    lowered = text.casefold()
    if any(re.search(pattern, lowered, re.IGNORECASE) for pattern in PROMOTIONAL_PATTERNS):
        return True
    return bool(URL_RE.search(lowered) and re.search(r"خرید|سفارش|فروش|order|buy|shop|دایرکت|تماس", lowered, re.I))


@dataclass
class FilterDecision:
    keep: bool
    reason: str
    signals: dict = field(default_factory=dict)


def cheap_x_filter(msg: ChatMessage, cfg, catalog=None) -> FilterDecision:
    text = norm(msg.text)
    bio = norm(msg.author_bio)
    without_urls = URL_RE.sub(" ", text)
    cleaned = re.sub(r"@\w+|#", " ", without_urls)
    meaningful = len(re.findall(r"[\wآ-ی]", cleaned))
    persian = len(re.findall(r"[آ-ی]", text))
    letters = len(re.findall(r"[A-Za-zآ-ی]", text))
    ratio = persian / max(1, letters)
    lang = (msg.lang or "").casefold()
    intent = any(norm(term) in text for term in INTENT_TERMS)
    signals = {"lang": lang, "persian_ratio": ratio, "intent": intent, "meaningful_chars": meaningful}
    if lang not in {"", "und", "fa", "per", "en"}:
        return FilterDecision(False, "lang", signals)
    if lang == "en" and not cfg.x_allow_finglish:
        return FilterDecision(False, "lang", signals)
    if lang in {"", "und"} and ratio < cfg.x_min_persian_ratio and not cfg.x_allow_finglish:
        return FilterDecision(False, "lang", signals)
    if meaningful < cfg.x_min_chars:
        return FilterDecision(False, "link_only" if not re.search(r"[\wآ-ی]", cleaned) else "too_short", signals)
    promotional = is_promotional_post(msg.text)
    shop_bio = any(term in bio for term in SHOP_TERMS)
    cta = bool(re.search(r"خرید|سفارش|فروش|order|buy|shop|دایرکت|تماس", text, re.I))
    has_link = bool(URL_RE.search(msg.text))
    if shop_bio and (cta or has_link or promotional):
        return FilterDecision(False, "shop_bio", signals)
    if promotional:
        return FilterDecision(False, "promotional", signals)
    if not intent and catalog:
        docs = [f"{product.title} {product.description} {' '.join(product.tags)}" for product in catalog.products]
        if docs:
            score = float(BM25(docs).scores(text).max())
            signals["catalog_bm25"] = score
            if score < cfg.x_min_relevance:
                return FilterDecision(False, "no_intent_signal", signals)
    return FilterDecision(True, "ok", signals)
