"""Conservative, inexpensive filters for independent public X posts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from need_engine.schemas import ChatMessage
from need_engine.text import BM25, norm
from shared.x_filters import PROMOTIONAL_PATTERNS, is_promotional_post, is_political_x_post, URL_RE

EXPLICIT_INTENT_TERMS = (
    "میخوام بخرم", "می‌خوام بخرم", "کجا بخرم", "چی بخرم", "قصد خرید", "خریدارم",
    "خریدار", "خریداری کنم", "تهیه کنم", "سفارش بدم", "دنبال خریدم", "باید بخرم",
    "میخوام تهیه", "می‌خوام تهیه", "میخوام بگیرم", "می‌خوام بگیرم", "میخوام سفارش",
    "می‌خوام سفارش", "از کجا بخرم", "از کجا تهیه", "از کجا بگیرم", "قیمت خرید", "هزینه خرید",
)

RECOMMENDATION_INTENT_TERMS = (
    "دنبال", "سراغ", "معرفی کنید", "معرفی کنین", "معرفی کن", "نیاز دارم",
    "لازم دارم", "پیشنهاد بدید", "پیشنهاد بدین", "پیشنهاد میدید", "پیشنهاد میدین",
    "پیشنهاد می‌کنید", "پیشنهاد میکنید", "راهنمایی کنید", "راهنمایی کنین",
    "توصیه می‌کنید", "توصیه میکنید", "چی پیشنهاد", "کدوم مدل", "کسی سراغ داره",
    "چی بگیرم", "کدوم رو پیشنهاد", "میخوام", "می‌خوام", "می خواهم",
)

COMPARISON_INTENT_TERMS = (
    "جایگزین", "مقایسه", "بهتره یا", "ارزش خرید داره", "ارزش خرید",
    "کدوم بهتره", "کدومش بهتره", "بین این دوتا", "بین این دو", "نظرتون درباره",
    "نظرتون راجع", "تجربه استفاده از", "ازش راضی هستید", "سوییچ به", "کوچ به",
)

GENERIC_INTENT_TERMS = ("پیشنهاد", "راهنمایی", "توصیه")

# Combined tuple preserved for backward compatibility
INTENT_TERMS = EXPLICIT_INTENT_TERMS + RECOMMENDATION_INTENT_TERMS + COMPARISON_INTENT_TERMS + GENERIC_INTENT_TERMS
SHOP_TERMS = ("فروشگاه", "ارسال به سراسر", "سفارش", "shop", "store", "official", "فروش")


@dataclass
class FilterDecision:
    keep: bool
    reason: str
    signals: dict = field(default_factory=dict)


def get_catalog_bm25(catalog) -> BM25 | None:
    """Retrieves or builds cached BM25 index on catalog.products."""
    if catalog is None:
        return None
    products = getattr(catalog, "products", None)
    if not products:
        return getattr(catalog, "bm25", None)
    cache = getattr(catalog, "_x_bm25_cache", None)
    cache_key = tuple(getattr(p, "product_id", id(p)) for p in products)
    if cache is not None and cache[0] == cache_key:
        return cache[1]
    if getattr(catalog, "bm25", None) is not None and getattr(catalog, "_bm25_cached", False):
        return catalog.bm25
    docs = []
    cards = getattr(catalog, "cards", {}) or {}
    for p in products:
        c = cards.get(getattr(p, "product_id", None))
        aliases = " ".join(getattr(c, "aliases", []) or [])
        problems = " ".join(getattr(c, "problems_solved", []) or [])
        desc = getattr(p, "description", "") or ""
        tags = " ".join(getattr(p, "tags", []) or [])
        cat_kw = " ".join(getattr(p, "category_keywords", []) or [])
        doc = f"{getattr(p, 'title', '')} {getattr(p, 'product_type', '')} {desc} {tags} {cat_kw} {aliases} {problems}".strip()
        docs.append(doc)
    bm25 = BM25(docs) if docs else None
    try:
        catalog._x_bm25_cache = (cache_key, bm25)
    except Exception:
        pass
    return bm25


def _detect_intent(text: str, cfg) -> tuple[bool, str, float]:
    """Classifies intent into explicit purchase > recommendation ask > comparison/switch.

    Very generic terms ('پیشنهاد', 'راهنمایی', 'توصیه') alone are not sufficient.
    """
    weights = getattr(cfg, "x_intent_weights", None) or {
        "explicit": 1.0, "recommendation": 0.8, "comparison": 0.6, "general": 0.0,
    }
    for term in EXPLICIT_INTENT_TERMS:
        if norm(term) in text:
            return True, "explicit", float(weights.get("explicit", 1.0))
    for term in RECOMMENDATION_INTENT_TERMS:
        if norm(term) in text:
            return True, "recommendation", float(weights.get("recommendation", 0.8))
    for term in COMPARISON_INTENT_TERMS:
        if norm(term) in text:
            return True, "comparison", float(weights.get("comparison", 0.6))
    return False, "none", 0.0


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

    has_intent, intent_group, intent_weight = _detect_intent(text, cfg)
    signals = {
        "lang": lang,
        "persian_ratio": ratio,
        "intent": has_intent,
        "intent_group": intent_group,
        "intent_weight": intent_weight,
        "meaningful_chars": meaningful,
    }

    if lang not in {"", "und", "fa", "per", "en"}:
        return FilterDecision(False, "lang", signals)
    if lang == "en" and not cfg.x_allow_finglish:
        return FilterDecision(False, "lang", signals)
    if lang in {"", "und"} and ratio < cfg.x_min_persian_ratio and not cfg.x_allow_finglish:
        return FilterDecision(False, "lang", signals)
    if meaningful < cfg.x_min_chars:
        return FilterDecision(False, "link_only" if not re.search(r"[\wآ-ی]", cleaned) else "too_short", signals)

    promotional = is_promotional_post(msg.text)
    if is_political_x_post(msg.text):
        return FilterDecision(False, "semantic_political", signals)
    shop_bio = any(term in bio for term in SHOP_TERMS)
    cta = bool(re.search(r"خرید|سفارش|فروش|order|buy|shop|دایرکت|تماس", text, re.I))
    has_link = bool(URL_RE.search(msg.text))
    if shop_bio and (cta or has_link or promotional):
        return FilterDecision(False, "shop_bio", signals)
    if promotional:
        return FilterDecision(False, "promotional", signals)

    # Topic gate via cached BM25 index on catalog
    has_topic = True
    topic_score = 1.0
    bm25 = get_catalog_bm25(catalog) if catalog else None
    if bm25 is not None:
        score = float(bm25.scores(text).max())
        signals["catalog_bm25"] = score
        has_topic = score >= cfg.x_min_relevance
        topic_score = min(1.0, score / max(cfg.x_min_relevance * 2.0, 0.15))
    else:
        signals["catalog_bm25"] = None

    # Author signals
    author_score = 0.2 if shop_bio else (1.0 if not bio else 0.9)
    signals["author_signals"] = author_score

    # Compute deterministic 0..100 score
    w_intent = getattr(cfg, "x_score_intent_weight", 45.0)
    w_topic = getattr(cfg, "x_score_topic_weight", 35.0)
    w_author = getattr(cfg, "x_score_author_weight", 20.0)
    tot_w = max(1.0, w_intent + w_topic + w_author)
    score_raw = (intent_weight * w_intent) + (topic_score * w_topic) + (author_score * w_author)
    score_100 = round(min(100.0, max(0.0, score_raw * (100.0 / tot_w))), 2)

    signals["score"] = score_100
    signals["score_breakdown"] = {
        "intent_weight": round(intent_weight, 2),
        "topic_score": round(topic_score, 2),
        "author_signals": round(author_score, 2),
    }

    # Strict check: post must have BOTH valid intent and relevant topic (if catalog given)
    if not has_intent:
        return FilterDecision(False, "no_intent", signals)
    if catalog is not None and getattr(catalog, "products", None) and not has_topic:
        return FilterDecision(False, "no_topic", signals)

    min_score = getattr(cfg, "x_filter_min_score", 25.0)
    if score_100 < min_score:
        return FilterDecision(False, "low_score", signals)

    return FilterDecision(True, "ok", signals)
