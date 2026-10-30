"""LLM-written X search terms per seller/category family (names people type, problems in buyer voice, brand/model).

Terms are cached in the need-engine kv table per family and only re-generated when family products, category or seller
notes change, so the collector (which rebuilds queries every cycle) pays once per family version. Failures fall back to the
deterministic terms in ``intent_queries``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from workers.x_collector.intent_queries import _FA, clean_term, short_terms

log = logging.getLogger("x_collector.llm_terms")
PROMPT_VERSION = "11"
KV_PREFIX = "x_qterms:"
X_QUERY_TERMS_SYSTEM = """You are a senior search-query engineer for Persian (Farsi) buyer discovery on X/Twitter.
You receive product FAMILIES. A family = one seller + one category node (e.g. «پوشاک > مردانه > پیراهن») plus the
titles of products in it and the seller's NOTES about the target customer (e.g. wholesale, retail, specific audience).

Your mission is to generate natural, realistic, broad Persian search phrases (1, 2, or 3 words) that real buyers, shoppers,
and people with genuine purchase or recommendation intent tweet on X.

CRITICAL RULES:
1. Every phrase MUST be 1, 2, or 3 Persian words. Never 4 or more.
2. NATURAL BUYER INTENT: Write phrases people actually tweet when seeking to buy, looking for recommendations, or asking about a product or service.
   - High-intent buying & recommendations: «خرید لباس مردانه», «خرید پیراهن مردانه», «پیشنهاد لباس», «معرفی پیراهن», «قیمت پیراهن مردانه», «خرید کت شلوار».
   - Common category & product searches: «لباس مردانه», «پیراهن مردانه», «کت و شلوار», «پیراهن اسپرت», «لباس مجلسی مردانه», «پوشاک مردانه».
   - Situational/Need phrases: «پیراهن نخی», «لباس خنک تابستانی», «پیراهن کژوال», «لباس راحتی», «پیراهن قواره بزرگ».
3. STRICTLY FORBIDDEN:
   - NEVER generate awkward, weird, or purely negative phrases like «لباس نامناسب», «پیراهن بدفرم», «لباس بی‌کیفیت», «لباس کثیف». Real buyers do not search for "unsuitable clothes"!
   - Never write single generic stop-words alone («کالا», «خدمات», «محصول»). Single product nouns like «پیراهن» or «کت» are acceptable, but 2-3 word natural phrases like «خرید پیراهن» or «لباس مردانه» are best.
   - No hashtags, quotes, @mentions, English words, punctuation, or emojis.
4. FAMILY ANCHORS: Every phrase must clearly be about this specific family's topic (e.g. men's shirts -> «پیراهن», «لباس مردانه»). Do not drift to sibling families (e.g. do not produce «شلوار» or «دامن» for a shirt family).
5. SELLER NOTES: If seller specifies wholesale («عمده»), students, specific gender or region, include natural phrases reflecting it (e.g. «خرید عمده پیراهن», «پیراهن عمده»).

THREE FORMATS (write 4 to 6 phrases for EACH):
 FORMAT A  pain_symptoms: Everyday situations, needs, or desired fits in natural buyer voice (e.g. «لباس خنک تابستانی», «پیراهن نخی خنک», «پیراهن قواره بزرگ», «لباس مجلسی شیک», «دندون درد»). NEVER write awkward negative terms like «نامناسب».
 FORMAT B  advice_requests: Asking for buying recommendations, advice, or shopping queries (e.g. «خرید لباس مردانه», «پیشنهاد لباس مردانه», «معرفی پیراهن», «خرید پیراهن مردانه», «قیمت پیراهن», «خرید کت شلوار»).
 FORMAT C  implicit_solutions: Core category names and search phrases buyers type directly (e.g. «پیراهن مردانه», «کت و شلوار», «لباس مردانه», «پیراهن کژوال», «پیراهن نخی», «پوشاک مردانه»).

ALSO RETURN:
 - names: 2 to 4 core category/topic names (1-3 words, e.g. «پیراهن مردانه», «لباس مردانه», «پیراهن نخی»).
 - negatives: 0 to 4 words to exclude seller ads/spam.
 - model: brand/model ONLY for tech/gadgets, otherwise null.

QUANTITY: 4-6 phrases per format. The search system selects at most 10 diverse phrases per family. Count the words of each phrase.

OUTPUT: strict JSON only:
{"items":[{"product_id":"<family id>","pain_symptoms":["..."],"advice_requests":["..."],"implicit_solutions":["..."],"names":["..."],"negatives":["..."],"model":null}]}"""
X_QUERY_TERMS_SYSTEM += """

FAMILY-DRIFT AND INTENT EXAMPLES (follow these exactly):
- Men's-shirt family: «خرید لباس مردانه», «پیراهن مردانه», «پیراهن نخی», «خرید پیراهن» are good; «تنگی شلوار» is wrong-family drift.
- Men's-shirt family: «پیراهن کژوال» and «پیشنهاد لباس» are good; «گرانی شومیز» belongs to another family.
- «لباس مجلسی مردانه», «پوشاک مردانه» and «خرید لباس مردانه» are all great, realistic buyer queries.
- «دندون درد», «ارتودنسی» and «ایمپلنت دندان» are good natural dental queries.
- NEVER write awkward or nonsensical phrases like «لباس نامناسب»!
"""
_LATIN = re.compile(r"[A-Za-z]{3,}")


MIN_PER_FORMAT = 3


def is_complete(terms: Any) -> bool:
    """All three formats are filled enough to search with; partial answers (truncated output) are never cached."""
    return isinstance(terms, dict) and all(len(terms.get(k) or []) >= MIN_PER_FORMAT
                                           for k in ("pain_symptoms", "advice_requests", "implicit_solutions"))


def _key(product: Any, card: Any, topic_context: dict[str, Any] | None = None) -> str:
    if hasattr(card, "model_dump"):
        card_data = card.model_dump(mode="json")
    elif isinstance(card, dict):
        card_data = card
    elif card is not None:
        card_data = {key: getattr(card, key, None) for key in ("what_it_is", "aliases", "problems_solved")}
    else:
        card_data = None
    topic = topic_context or {}
    context_data = {key: topic.get(key, []) for key in ("anchor_words", "other_family_names")}
    payload = [PROMPT_VERSION, product.content_hash(), card_data, context_data]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def sanitize(raw: Any) -> dict[str, Any]:
    """Keeps only 1-3 word phrases (longer ones are dropped, never truncated)."""
    raw = raw if isinstance(raw, dict) else {}
    model = clean_term(raw.get("model") or "")
    model = model if model and model.lower() not in {"null", "none"} and len(model.split()) <= 2 and len(model) >= 3 else None
    pain = short_terms(raw.get("pain_symptoms") or raw.get("problems"), 8)
    advice = short_terms(raw.get("advice_requests") or raw.get("alternatives"), 8)
    solutions = short_terms(raw.get("implicit_solutions"), 8)
    return {
        "pain_symptoms": pain,
        "advice_requests": advice,
        "implicit_solutions": solutions,
        "names": short_terms(raw.get("names"), 4, strip_noise=True),
        "negatives": short_terms(raw.get("negatives"), 5),
        "model": model,
        # backward-compatibility keys read by older callers
        "problems": pain,
        "alternatives": advice,
        "dissatisfactions": [],
        "complaints": [],
        "cues": [],
        "triplets": [],
    }


def _user(product: Any, card: Any, topic_context: dict[str, Any] | None = None) -> dict[str, Any]:
    item = {"product_id": str(getattr(product, "product_id", "")), "title": getattr(product, "title", ""),
            "type": getattr(product, "product_type", ""),
            "category": getattr(product, "category_path", ""), "description": (getattr(product, "description", "") or "")[:1400]}
    if card is not None:
        item.update(what_it_is=getattr(card, "what_it_is", None), aliases=getattr(card, "aliases", None),
                    problems_solved=getattr(card, "problems_solved", None))
    topic_context = topic_context or {}
    item["OTHER FAMILIES OF THIS SELLER (never write phrases about these)"] = topic_context.get("other_family_names", [])
    item["ANCHOR WORDS you may use"] = topic_context.get("anchor_words", [])
    return item


def llm_terms(products: list[Any], cards: dict[str, Any], store: Any, llm: Any, model: str,
              batch_size: int = 15) -> dict[str, dict[str, Any]]:
    """{product_id: {"names", "problems", "model"}}; products whose generation failed are missing (fallback)."""
    cached = store.get_prefix(KV_PREFIX)
    from workers.x_collector.families import family_topic_contexts
    contexts = family_topic_contexts(products, cards) if products and hasattr(products[0], "member_product_ids") else {}
    out: dict[str, dict[str, Any]] = {}
    todo = []
    for product in products:
        key = _key(product, cards.get(product.product_id), contexts.get(str(product.product_id)))
        hit = cached.get(KV_PREFIX + product.product_id)
        if isinstance(hit, dict) and hit.get("h") == key and is_complete(hit.get("terms")):
            out[product.product_id] = hit["terms"]
        else:
            todo.append((product, key))
    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        user = json.dumps({"products": [_user(p, cards.get(p.product_id), contexts.get(str(p.product_id)))
                                         for p, _ in chunk]}, ensure_ascii=False)
        try:
            data, _ = llm.complete_json("x_query_extract", model, X_QUERY_TERMS_SYSTEM, user, max_tokens=4096,
                                        ref="x_query_terms")
        except Exception as exc:   # quota/network: keep deterministic queries, retry next cycle
            log.warning("X query terms: LLM failed for %d products: %s", len(chunk), exc)
            continue
        items = {str(i.get("product_id")): i for i in (data or {}).get("items", []) if isinstance(i, dict)}
        for product, key in chunk:
            if product.product_id not in items:
                continue
            terms = sanitize(items[product.product_id])
            if is_complete(terms):
                store.set(KV_PREFIX + product.product_id, {"h": key, "terms": terms})
            else:
                log.warning("X query terms for product %s incomplete (%d/%d/%d); not cached, retrying next cycle",
                            product.product_id, len(terms["pain_symptoms"]), len(terms["advice_requests"]),
                            len(terms["implicit_solutions"]))
            out[product.product_id] = terms
    return out
