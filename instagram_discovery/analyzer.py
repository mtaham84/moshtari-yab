"""Heuristic screening for product-selling Instagram posts."""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlparse


PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
CONFIDENT_CATEGORY_SCORE = 0.75
MIN_CAPTION_LENGTH = 40


class CategoryKnowledge(Protocol):
    def categories_for(self, source: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]: ...


class StaticCategoryKnowledge:
    """Read-only category data supplied by a seed or a local category file."""

    def __init__(self, *categories: Mapping[str, Any]):
        self._categories = tuple(categories)

    def categories_for(self, source: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        business_id = source.get("business_id")
        return tuple(
            category for category in self._categories
            if business_id is None or category.get("business_id") in (None, business_id)
        )


def knowledge_from_seed(seed: Mapping[str, Any]) -> StaticCategoryKnowledge:
    if seed.get("kind") != "hashtag" or not seed.get("category_id"):
        return StaticCategoryKnowledge()
    return StaticCategoryKnowledge({
        "id": seed["category_id"],
        "business_id": seed.get("business_id"),
        "name": seed.get("category", ""),
        "full_path": seed.get("category_path", ""),
        "keywords": seed.get("terms", []),
        "hashtags": [seed.get("value", "")],
        "products": [
            {"id": product_id, "name": product_name}
            for product_id, product_name in zip(seed.get("product_ids", []), seed.get("products", []))
        ],
    })


def knowledge_from_seeds(seeds: Sequence[Mapping[str, Any]], business_id: Any) -> StaticCategoryKnowledge:
    categories: dict[Any, dict[str, Any]] = {}
    for seed in seeds:
        if seed.get("kind") != "hashtag" or seed.get("business_id") != business_id:
            continue
        category_id = seed.get("category_id")
        if not category_id:
            continue
        category = categories.setdefault(category_id, {
            "id": category_id, "business_id": business_id,
            "name": seed.get("category", ""), "full_path": seed.get("category_path", ""),
            "keywords": [], "hashtags": [], "products": [],
        })
        category["keywords"] = list(dict.fromkeys([*category["keywords"], *seed.get("terms", [])]))
        category["hashtags"] = list(dict.fromkeys([*category["hashtags"], seed.get("value", "")]))
        products = {product["id"]: product for product in category["products"]}
        products.update({
            product_id: {"id": product_id, "name": product_name}
            for product_id, product_name in zip(seed.get("product_ids", []), seed.get("products", []))
        })
        category["products"] = list(products.values())
    return StaticCategoryKnowledge(*categories.values())


def normalize_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).translate(PERSIAN_DIGITS)
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = "".join(" " if char == chr(0x200c) else "" if char == chr(0x200f) else char for char in text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _contains_phrase(text: str, phrase: Any) -> bool:
    normalized = normalize_text(phrase)
    return bool(normalized and re.search(r"(?<!\w)" + re.escape(normalized) + r"(?!\w)", text))


def _product_matches(caption: str, products: Sequence[Any]) -> list[Mapping[str, Any]]:
    caption_tokens = set(re.findall(r"[\w]+", normalize_text(caption)))
    matches = []
    for product in products:
        if not isinstance(product, Mapping) or not product.get("name"):
            continue
        product_tokens = set(re.findall(r"[\w]+", normalize_text(product["name"])))
        shared = caption_tokens & product_tokens
        if len(shared) >= min(2, len(product_tokens)) and len(shared) / max(1, len(product_tokens)) >= 0.6:
            matches.append(product)
    return matches


def is_persian_script_caption(value: Any) -> bool:
    """Accept Arabic-script captions only; reject English, Finglish, and mixed Latin text.

    URLs and tagged account names do not determine caption language. Hashtags do,
    so an English hashtag on an otherwise Persian caption is treated as mixed.
    This is a conservative lightweight filter, not a full language detector.
    """
    text = str(value or "")
    text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<![\w.])@[A-Za-z0-9._]+", " ", text)
    has_arabic_script_letter = False
    has_latin_letter = False
    for char in text:
        if not unicodedata.category(char).startswith("L"):
            continue
        name = unicodedata.name(char, "")
        if "LATIN" in name:
            has_latin_letter = True
        elif "ARABIC" in name:
            has_arabic_script_letter = True
        else:
            # A caption containing another alphabet is not Persian-only.
            return False
    if not has_arabic_script_letter or has_latin_letter:
        return False
    # Persian-only letters and common Persian forms provide a language signal
    # beyond merely using the shared Arabic script. This intentionally errs on
    # the side of skipping uncertain captions.
    if any(char in text for char in "پچژگ"):
        return True
    normalized = normalize_text(text)
    persian_markers = (
        "لطفا", "لطفاً", "میشه", "می شه", "می شود", "میشود", "میخوام", "می خوام",
        "هستش", "هستن", "رو کامنت", "عدد", "تومان", "ریال", "ارسال", "موجود",
        "سفارش", "قیمت", "خرید", "فروش", "تخفیف", "دایرکت", "کامنت", "محصول",
    )
    return any(normalize_text(marker) in normalized for marker in persian_markers)


def _first(row: dict, *names: str) -> Any:
    for name in names:
        if row.get(name) not in (None, ""):
            return row[name]
    return None


def _post_url(row: dict) -> str:
    value = _first(row, "url", "permalink", "post_url", "link")
    if isinstance(value, str):
        parsed = urlparse(value.strip())
        # A profile URL is not enough to reconnect to a particular post later.
        if (parsed.scheme in {"http", "https"}
                and parsed.hostname in {"instagram.com", "www.instagram.com"}
                and re.fullmatch(r"/(?:p|reel|tv)/[A-Za-z0-9_-]{5,32}/?", parsed.path)):
            return value.strip()
    shortcode = _first(row, "shortcode", "code", "media_code")
    if isinstance(shortcode, str) and re.fullmatch(r"[A-Za-z0-9_-]{5,20}", shortcode):
        return f"https://www.instagram.com/p/{shortcode}/"
    return ""


def _author(row: dict) -> str:
    direct = _first(row, "username", "user", "owner_username", "author")
    if isinstance(direct, dict):
        direct = _first(direct, "username", "user_name") or ""
    value = str(direct or "").strip()
    if value.startswith("@"):
        value = value[1:]
    if "instagram.com/" in value:
        parts = [part for part in urlparse(value).path.split("/") if part]
        value = parts[0] if parts else ""
    return value if re.fullmatch(r"[A-Za-z0-9._]{1,30}", value) else ""


def load_categories(rows: list[dict]) -> list[dict]:
    categories = []
    for row in rows:
        full_path = str(row.get("full_path") or row.get("name") or "").strip()
        name = str(row.get("name") or full_path.split(" > ")[-1]).strip()
        if not name:
            continue
        keywords = row.get("keywords") or row.get("category_keywords") or []
        if isinstance(keywords, str):
            keywords = re.split(r"[,،|]", keywords)
        terms = [str(k).strip() for k in keywords if str(k).strip()]
        terms.extend(part.strip() for part in full_path.split(" > ") if part.strip())
        categories.append({
            "id": row.get("id"),
            "name": name,
            "full_path": full_path,
            "keywords": list(dict.fromkeys(terms)),
        })
    return categories


def _category_decision(caption: str, hashtags: Sequence[str], source: Mapping[str, Any],
                       knowledge: CategoryKnowledge) -> dict[str, Any]:
    normalized_caption = normalize_text(caption)
    normalized_hashtags = {normalize_text(tag.lstrip("#")) for tag in hashtags}
    source_hashtag = normalize_text(source.get("value", ""))
    source_hashtag_is_post_hashtag = source_hashtag in normalized_hashtags
    ranked: list[tuple[float, dict[str, Any], dict[str, list[str]]]] = []
    for raw_category in knowledge.categories_for(source):
        category = dict(raw_category)
        terms = category.get("keywords", [])
        if isinstance(terms, str):
            terms = re.split(r"[,،|]", terms)
        category_hashtags = category.get("hashtags", [])
        products = category.get("products", [])
        caption_hits = sorted({str(term).strip() for term in terms if str(term).strip()
                               and _contains_phrase(normalized_caption, term)})
        product_hits = sorted({str(product.get("name", "")).strip() for product in products
                               if isinstance(product, Mapping) and product.get("name")
                               and _contains_phrase(normalized_caption, product["name"])})
        hashtag_hits = sorted({str(tag).strip().lstrip("#") for tag in category_hashtags
                               if str(tag).strip() and normalize_text(str(tag).strip().lstrip("#"))
                               in normalized_hashtags})
        if source_hashtag_is_post_hashtag:
            hashtag_hits = [tag for tag in hashtag_hits if normalize_text(tag) != source_hashtag]
        matched_products = _product_matches(caption, products)
        product_hits = sorted({str(product["name"]) for product in matched_products})
        evidence = {"caption_excerpt": caption[:500], "caption_terms": caption_hits, "product_terms": product_hits,
                    "post_hashtags": hashtag_hits}
        score = min(1.0, 0.50 * bool(caption_hits) + 0.25 * bool(product_hits)
                    + 0.25 * bool(hashtag_hits) + 0.10 * max(0, len(caption_hits) + len(product_hits) - 1))
        if score:
            ranked.append((score, category, evidence))

    source_name = "seller_category_seed" if source.get("kind") == "hashtag" else "category_knowledge"
    knowledge_source = {"kind": source_name, "business_id": source.get("business_id"),
                        "category_id": source.get("category_id")}
    base = {"result": "rejected", "category_id": None, "category": None,
            "category_candidate_id": None, "category_candidate": None,
            "category_path": None,
            "product_id": None, "product": None, "product_candidate_id": None,
            "product_candidate": None, "score": 0.0,
            "reason": {"code": "no_matching_evidence", "evidence": {"caption_excerpt": caption[:500],
                                                                  "caption_terms": [],
                                                                  "product_terms": [], "post_hashtags": []}},
            "knowledge_source": knowledge_source}
    ranked.sort(key=lambda row: row[0], reverse=True)
    too_short = len(normalized_caption) <= MIN_CAPTION_LENGTH
    if too_short:
        score, _, evidence = ranked[0] if ranked else (0.0, {}, base["reason"]["evidence"])
        if ranked:
            products = ranked[0][1].get("products", [])
            matched_names = ranked[0][2]["product_terms"]
            product = next((item for item in products if isinstance(item, Mapping)
                            and item.get("name") in matched_names), None)
            base.update(category_candidate_id=ranked[0][1].get("id"),
                        category_candidate=ranked[0][1].get("name"),
                        category_path=ranked[0][1].get("full_path"),
                        product_candidate_id=product.get("id") if product else None,
                        product_candidate=product.get("name") if product else None,
                        knowledge_source={"kind": source_name,
                                          "business_id": ranked[0][1].get("business_id"),
                                          "category_id": ranked[0][1].get("id")})
        base.update(result="human_review", score=round(score, 2),
                    reason={"code": "caption_too_short", "evidence": evidence})
        return base
    if not ranked:
        return base

    best_score, best, evidence = ranked[0]
    if len(ranked) > 1 and ranked[1][0] == best_score:
        base.update(result="human_review", category_candidate_id=best.get("id"),
                    category_candidate=best.get("name"), category_path=best.get("full_path"),
                    product_candidate=next(iter(evidence["product_terms"]), None),
                    score=round(best_score, 2),
                    reason={"code": "ambiguous_category", "evidence": evidence,
                            "alternatives": [row[1].get("name") for row in ranked if row[0] == best_score]},
                    knowledge_source={"kind": source_name, "business_id": best.get("business_id"),
                                      "category_id": best.get("id")})
        return base

    products = best.get("products", [])
    matched_products = _product_matches(caption, products)
    product = matched_products[0] if len(matched_products) == 1 else None
    confident = best_score >= CONFIDENT_CATEGORY_SCORE
    knowledge_source["category_id"] = best.get("id")
    base.update(
        result="confident_match" if confident else "human_review",
        category_candidate_id=best.get("id"), category_candidate=best.get("name"),
        product_candidate_id=product.get("id") if product else None,
        product_candidate=product.get("name") if product else None,
        category_path=best.get("full_path"),
        category_id=best.get("id") if confident else None,
        category=best.get("name") if confident else None,
        product_id=product.get("id") if confident and product else None,
        product=product.get("name") if confident and product else None,
        score=round(best_score, 2),
        reason={"code": "matched" if confident else ("caption_too_short" if too_short else "uncertain_score"),
                "evidence": evidence},
        knowledge_source={"kind": source_name, "business_id": best.get("business_id"),
                          "category_id": best.get("id")},
    )
    return base


def _product_name_candidate(caption: str, category_hits: list[str]) -> tuple[str | None, float]:
    """Extract a short caption fragment for review; this is not an authoritative product name."""
    text = re.split(r"[\n.!؟?؛؛|]+", caption, maxsplit=1)[0]
    text = re.sub(r"https?://\S+|www\.\S+|@[\w.]+|#[\wآ-ی]+", " ", text)
    for phrase in ("برای خرید", "برای سفارش", "ثبت سفارش", "سفارش دایرکت", "قیمت دایرکت",
                   "دایرکت برای قیمت", "کامنت کن", "کامنت کنید", "کامنت بگذار", "کامنت بذار",
                   "کامنت بزار", "عدد یک", "عدد 1", "تومان", "ریال"):
        match = re.search(re.escape(phrase), text, flags=re.IGNORECASE)
        if match:
            text = text[:match.start()]
    text = re.sub(r"^[\s:،,؛|]+|[\s:،,؛|]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) < 3:
        return None, 0.0
    confidence = 0.25
    if category_hits:
        confidence += 0.25
    if re.search(r"(?:مدل|جنس|سایز|رنگ|مناسب|طرح)", text):
        confidence += 0.15
    return text[:100], min(0.65, confidence)


def analyze_post(row: dict, source: Mapping[str, Any], categories: Sequence[Mapping[str, Any]] | CategoryKnowledge,
                 owner_username: str = "") -> dict[str, Any]:
    caption = str(_first(row, "caption", "text", "description") or "").strip()
    hashtags_value = _first(row, "hashtags", "tags") or []
    if isinstance(hashtags_value, str):
        hashtags_value = re.findall(r"#[\wآ-ی]+", hashtags_value)
    hashtags = [str(tag).strip().lstrip("#") for tag in hashtags_value if str(tag).strip()]
    category_knowledge = categories if hasattr(categories, "categories_for") else StaticCategoryKnowledge(*categories)
    decision = _category_decision(caption, hashtags, source, category_knowledge)
    persian_script_caption = is_persian_script_caption(caption)
    # Non-Persian / mixed-script captions are archived as observations, but no
    # ad, price, engagement-bait, product, or category analysis runs on them.
    normalized = normalize_text(caption) if persian_script_caption else ""
    # OpenCLI schemas can vary by version and some output paths return only a
    # short caption. Treat a 100+ character caption as likely complete; short
    # captions remain uncertain and need manual review.
    caption_may_be_truncated = 0 < len(caption) <= 100
    author = _author(row) or owner_username or (source.get("value") if source.get("kind") == "user" else "")

    product_terms = [
        "سفارش", "ثبت سفارش", "خرید", "فروش", "فروشی", "موجوده", "موجود است",
        "موجود شد", "تخفیف", "ارسال داریم", "ارسال به سراسر", "محصول", "برای خرید",
    ]
    matched_product_terms = sorted(
        {term for term in product_terms if normalize_text(term) in normalized}, key=normalize_text
    )
    bait_terms = [
        "کامنت کن", "کامنت کنید", "کامنت بگذار", "کامنت بذار", "کامنت بزار",
        "کامنت 1", "کامنت عدد 1", "عدد 1 رو کامنت کن", "عدد 1 را کامنت کن",
        "عدد 1", "عدد یک", "بنویس 1", "بنویس یک", "کلمه 1", "کلمه یک",
        "1 رو کامنت کن", "1 را کامنت کن", "عدد 1 رو بفرست", "عدد 1 را بفرست",
        "1 بذار", "1 بزار", "یک بذار", "یک بزار", "زیر پست بنویس",
        "زیرش بنویس", "تو کامنت بنویس",
    ]
    comment_bait_terms = [term for term in bait_terms if "کامنت" in normalize_text(term) or "زیر" in normalize_text(term)]
    matched_bait = sorted(
        {term for term in bait_terms if normalize_text(term) in normalized}, key=normalize_text
    )
    matched_comment_bait = sorted(
        {term for term in comment_bait_terms if normalize_text(term) in normalized}, key=normalize_text
    )
    has_explicit_price = bool(
        re.search(r"(?:قیمت|فقط)\s*[:：]?\s*\d{2,}", normalized)
        or re.search(r"\d[\d,٬.]*\s*(?:تومان|ریال)", normalized)
        or re.search(r"\d+\s*(?:هزار|میلیون)\s*(?:تومان|ریال)", normalized)
    )
    hidden_price_terms = [
        "قیمت در دایرکت", "دایرکت برای قیمت", "برای قیمت", "استعلام قیمت",
        "قیمت رو دایرکت", "قیمت را دایرکت", "قیمت تو دایرکت", "قیمت داخل دایرکت",
        "قیمت دایرکت", "برای اطلاع از قیمت", "قیمت رو بفرستم", "قیمت را بفرستم",
        "قیمت ارسال میشه", "قیمت ارسال می شود",
    ]
    matched_hidden_price = sorted(
        {term for term in hidden_price_terms if normalize_text(term) in normalized}, key=normalize_text
    )
    if "دایرکت" in normalized and "قیمت" in normalized and not matched_hidden_price:
        matched_hidden_price.append("قیمت با دایرکت")
    if has_explicit_price:
        matched_hidden_price = []

    # The initial use case is specifically sales posts without a stated price
    # that try to pull people into the comments. A category match is also weak
    # evidence that the caption names a product, even without a sales verb.
    sales_signal = bool(matched_product_terms)
    interaction_signal = bool(matched_comment_bait)
    hidden_price_signal = bool(matched_hidden_price)
    explicit_price_signal = has_explicit_price
    category = next((item for item in category_knowledge.categories_for(source)
                     if item.get("id") == decision.get("category_id") and decision["result"] == "confident_match"), None)
    category_hits = decision["reason"]["evidence"]["caption_terms"]
    category_signal = decision["result"] == "confident_match"
    if interaction_signal and "قیمت" in normalized and not explicit_price_signal:
        hidden_price_signal = True
        if "قیمت با دعوت به کامنت" not in matched_hidden_price:
            matched_hidden_price.append("قیمت با دعوت به کامنت")
    score = min(1.0, 0.45 * (sales_signal or category_signal) + 0.45 * interaction_signal
                + 0.25 * hidden_price_signal)
    if explicit_price_signal:
        score *= 0.35
    advertisement = interaction_signal and not explicit_price_signal and (sales_signal or category_signal or hidden_price_signal)
    product_name, product_confidence = _product_name_candidate(caption, category_hits)

    media_id = _first(row, "media_id", "id", "pk")
    url = _post_url(row)
    complete = bool(caption and author and (url or media_id))
    review_reasons = []
    if not caption:
        review_reasons.append("caption_missing")
    elif not persian_script_caption:
        review_reasons.append("caption_not_persian")
    elif caption_may_be_truncated:
        review_reasons.append("caption_may_be_truncated")
    if not author:
        review_reasons.append("seller_unknown")
    if not (url or media_id):
        review_reasons.append("stable_post_id_missing")
    if advertisement and not category:
        review_reasons.append("category_unmatched")
    if advertisement and not product_name:
        review_reasons.append("product_name_unmatched")

    analysis = {
        "source_platform": "instagram",
        "source_kind": source.get("kind"),
        "source_value": source.get("value"),
        "post_id": str(media_id) if media_id is not None else None,
        "post_url": url or None,
        "seller_username": author or None,
        "seller_candidate": advertisement and bool(author),
        "caption": caption,
        "caption_is_persian_script": persian_script_caption,
        "product_name_candidate": product_name if advertisement else None,
        "product_name_confidence": product_confidence if advertisement else 0.0,
        "caption_length": len(caption),
        "caption_may_be_truncated": caption_may_be_truncated,
        "likes": _first(row, "likes", "like_count", "play_count"),
        "comments_count": _first(row, "comments", "comment_count"),
        "media_type": _first(row, "type", "media_type"),
        "posted_at": _first(row, "date", "taken_at", "timestamp"),
        "is_product_ad_candidate": advertisement,
        "ad_confidence": round(score, 2),
        "engagement_bait_signals": matched_bait,
        "comment_bait_signals": matched_comment_bait,
        "has_explicit_price": has_explicit_price,
        "price_status": "explicit" if has_explicit_price else ("hidden" if matched_hidden_price else "not_found"),
        "price_hidden_signals": matched_hidden_price,
        "product_language_signals": matched_product_terms,
        "category_candidate": decision["category"],
        "category_path_candidate": category.get("full_path") if category else None,
        "category_id_candidate": decision["category_id"],
        "category_confidence": decision["score"] if decision["result"] == "confident_match" else 0.0,
        "category_keyword_hits": category_hits,
        "category_decision": decision,
        "decision_status": decision["result"],
        "human_review_required": decision["result"] == "human_review" or bool(advertisement and not complete),
        "ready_for_review": bool(advertisement and complete and decision["result"] == "confident_match"
                                   and product_name and not caption_may_be_truncated),
        "review_reasons": review_reasons,
        "raw": row,
    }
    analysis["hashtags"] = hashtags
    return analysis


def analyze_posts(rows: list[dict], source: Mapping[str, Any],
                  categories: Sequence[Mapping[str, Any]] | CategoryKnowledge, owner_username: str = "") -> list[dict[str, Any]]:
    return [analyze_post(row, source, categories, owner_username) for row in rows]
