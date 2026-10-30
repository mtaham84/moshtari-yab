"""LLM-written X search terms per product (names people type, problems in buyer voice, brand/model).

Terms are cached in the need-engine kv table per product and only re-generated when the product or its card changes,
so the collector (which rebuilds queries every cycle) pays once per product version. Failures fall back to the
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
PROMPT_VERSION = "6"
KV_PREFIX = "x_qterms:"
X_QUERY_TERMS_SYSTEM = """You are a senior search-query engineer for Persian (Farsi) buyer discovery on X/Twitter.
Goal: for each product, write SHORT, GENERIC, HIGH-RECALL Persian search phrases that surface real people who need this
kind of product or service right now. X search is exact-match: all words of a phrase must co-occur, so every extra word
makes a query exponentially rarer. Short, topical phrases win; long, specific ones return nothing.

HARD RULES (a phrase that breaks any rule is discarded by the system):
1. Every phrase is exactly 1 or 2 Persian words. NEVER 3 or more words. No sentences, no questions, no filler.
2. Use the COMMON NOUN for the category/need that ordinary people type: «سنگ نما», «سنگ ساختمانی», «دندان درد», «لپ تاپ»,
   «کنکور», «سئو سایت». Think: what 2 words would 1,000 different people use when they talk about this topic?
3. Be GENERIC and TOPICAL. Do NOT put in a phrase: colors, sizes, finishes, materials, brands, model numbers, prices,
   quantities, cities, adjectives or marketing words («شکلاتی», «شکسته», «لابی», «ارزان», «اصل», «مدل ۲۰۲۶», «درجه یک»).
   BAD: «سنگ تراورتن شکلاتی», «سنگ لابی شکسته», «لپ تاپ استوک ایسوس», «بهترین دندانپزشک تهران».
   GOOD: «سنگ تراورتن», «سنگ نما», «شوره سنگ», «لپ تاپ», «دندان درد», «ایمپلنت دندان».
4. Phrases must be natural colloquial Persian that a real person would tweet, not catalog language.
5. No X operators, quotes, hashtags, @mentions, emojis, punctuation, English words (except a well-known brand that is
   literally part of the product title).
6. Diversity: each phrase must differ in meaning or topic noun from the others. No near-duplicates (no plural/singular-only
   or spelling-only variants).
7. Entity-blind: do not use the seller's own brand or shop name.

THREE FORMATS (these are the search angles; write 5 to 8 phrases for EACH):
 FORMAT A  pain_symptoms: the person's own problem or frustration, as [problem word] + [topic noun], or [topic noun] +
           [symptom]. Examples: «شوره سنگ», «ترک سنگ», «دندان درد», «لپ تاپ هنگ», «نشتی آب».
 FORMAT B  advice_requests: the person asks others for a recommendation/experience: [advice word] + [topic noun].
           Advice words: پیشنهاد، معرفی، تجربه، کجا، کدوم، دنبال، راهنمایی. Examples: «پیشنهاد سنگبر», «معرفی دندانپزشک»,
           «تجربه ایمپلنت», «کجا لپتاپ».
 FORMAT C  implicit_solutions: direct need or purchase intent: [action word] + [topic noun].
           Action words: خرید، قیمت، سفارش، نیاز، دنبال، میخوام. Examples: «خرید سنگ», «قیمت تراورتن», «سفارش سنگ»,
           «دنبال دندانپزشک».

ALSO RETURN
 - names: 2 to 4 core topic names of the product/service (1 or 2 words each, the most generic correct nouns).
 - negatives: 0 to 4 single words typical of seller ads/spam for this topic (e.g. «تخفیف», «فروشگاه», «ارسال»).
 - model: brand + model ONLY for hardware/gadgets that people name (max 2 tokens), otherwise null.

QUANTITY: 5-8 phrases per format. Fewer than 5 finds nothing; more than 8 adds noise. Count words before answering.

OUTPUT: strict JSON only, no commentary:
{"items":[{"product_id":"...","pain_symptoms":["..."],"advice_requests":["..."],"implicit_solutions":["..."],"names":["..."],"negatives":["..."],"model":null}]}"""
_LATIN = re.compile(r"[A-Za-z]{3,}")


MIN_PER_FORMAT = 3


def is_complete(terms: Any) -> bool:
    """All three formats are filled enough to search with; partial answers (truncated output) are never cached."""
    return isinstance(terms, dict) and all(len(terms.get(k) or []) >= MIN_PER_FORMAT
                                           for k in ("pain_symptoms", "advice_requests", "implicit_solutions"))


def _key(product: Any, card: Any) -> str:
    payload = [PROMPT_VERSION, product.content_hash(), card.model_dump(mode="json") if card is not None else None]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def sanitize(raw: Any) -> dict[str, Any]:
    """Keeps only 1-2 word phrases (longer ones are dropped, never truncated)."""
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


def _user(product: Any, card: Any) -> dict[str, Any]:
    item = {"product_id": str(getattr(product, "product_id", "")), "title": getattr(product, "title", ""),
            "type": getattr(product, "product_type", ""),
            "category": getattr(product, "category_path", ""), "description": (getattr(product, "description", "") or "")[:400]}
    if card is not None:
        item.update(what_it_is=getattr(card, "what_it_is", None), aliases=getattr(card, "aliases", None),
                    problems_solved=getattr(card, "problems_solved", None))
    return item


def llm_terms(products: list[Any], cards: dict[str, Any], store: Any, llm: Any, model: str,
              batch_size: int = 15) -> dict[str, dict[str, Any]]:
    """{product_id: {"names", "problems", "model"}}; products whose generation failed are missing (fallback)."""
    cached = store.get_prefix(KV_PREFIX)
    out: dict[str, dict[str, Any]] = {}
    todo = []
    for product in products:
        key = _key(product, cards.get(product.product_id))
        hit = cached.get(KV_PREFIX + product.product_id)
        if isinstance(hit, dict) and hit.get("h") == key and is_complete(hit.get("terms")):
            out[product.product_id] = hit["terms"]
        else:
            todo.append((product, key))
    for start in range(0, len(todo), batch_size):
        chunk = todo[start:start + batch_size]
        user = json.dumps({"products": [_user(p, cards.get(p.product_id)) for p, _ in chunk]}, ensure_ascii=False)
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
