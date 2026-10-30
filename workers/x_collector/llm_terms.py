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
PROMPT_VERSION = "7"
KV_PREFIX = "x_qterms:"
X_QUERY_TERMS_SYSTEM = """You are a senior search-query engineer for Persian (Farsi) buyer discovery on X/Twitter.
You receive product FAMILIES. A family = one seller + one category node (for example «پوشاک > مردانه > پیراهن») plus the
titles of his products in it and his own NOTES about the ideal customer and the way he sells (for example "I make socks
and want shops that buy wholesale"). Write search phrases that surface real people who need what this family offers
right now. X search is exact-match: every word of a phrase must co-occur, so each extra word makes a query rarer.

HARD RULES (a phrase that breaks any rule is discarded by the system)
1. Every phrase is 1, 2 or 3 Persian words. Never 4 or more. No sentences, no questions, no filler.
2. SPECIFICITY LADDER - aim for the right rung:
   - TOO BROAD (rejected): «لباس», «کفش», «لوازم», «خدمات», «کالا», «خرید لباس».
   - GOOD, product type: «جوراب», «دامن», «کت», «ایمپلنت دندان», «کلاس کنکور».
   - BEST, type + one inherent attribute: «لباس مجلسی مردانه», «کت چرم», «دامن کوتاه», «جوراب ورزشی», «سنگ نما».
   - TOO NARROW (rejected): colours, sizes, finishes, brands, model numbers, cities, prices, adjectives such as «شکلاتی»,
     «شکسته», «ارزان», «بهترین», «اصل», «۲۰۲۶».
3. Phrases must be natural colloquial Persian that a real person would tweet AND that appears in many tweets (recall).
4. Use the family's own category and product titles to choose the topic nouns; the topic must be unmistakably this family,
   not a neighbour (a men's-shirt family must not produce «دامن»).
5. SELLER NOTES are binding: if the seller wants wholesale buyers, shop owners, companies, students, a city or a price
   segment, reflect it with natural words in the phrases (e.g. «عمده جوراب», «خرید عمده», «جوراب فروشی», «دانشجو پایتون»).
6. No X operators, quotes, hashtags, @mentions, emojis, punctuation or English words (except a well-known brand that is
   literally part of the product titles).
7. Diversity: every phrase differs in meaning or topic noun; no near-duplicates (plural/singular or spelling variants).
8. Entity-blind: never use the seller's brand or shop name.

THREE FORMATS (write 5 to 8 phrases for EACH)
 FORMAT A  pain_symptoms: the person's own problem or frustration - [topic noun] + [problem word], e.g. «شوره سنگ»,
           «دندان درد», «لپ تاپ کند», «پیراهن تنگ».
 FORMAT B  advice_requests: asking others for a recommendation or experience - [advice word] + [topic], e.g. «پیشنهاد
           سنگبر», «معرفی دندانپزشک», «تجربه ایمپلنت», «کجا کت مجلسی».
 FORMAT C  implicit_solutions: direct need or purchase intent - [action word] + [topic], e.g. «خرید عمده جوراب», «قیمت
           تراورتن», «سفارش دامن», «دنبال دندانپزشک».

ALSO RETURN
 - names: 2 to 4 core topic names of the family (1-3 words, the most natural nouns people use).
 - negatives: 0 to 4 single words typical of seller ads/spam for this topic.
 - model: brand + model ONLY for hardware/gadgets that people name (max 2 tokens), otherwise null.

QUANTITY: 5-8 phrases per format. Fewer than 5 finds nothing; more than 8 adds noise. Count the words of each phrase.

OUTPUT: strict JSON only:
{"items":[{"product_id":"<family id>","pain_symptoms":["..."],"advice_requests":["..."],"implicit_solutions":["..."],"names":["..."],"negatives":["..."],"model":null}]}"""
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


def _user(product: Any, card: Any) -> dict[str, Any]:
    item = {"product_id": str(getattr(product, "product_id", "")), "title": getattr(product, "title", ""),
            "type": getattr(product, "product_type", ""),
            "category": getattr(product, "category_path", ""), "description": (getattr(product, "description", "") or "")[:1400]}
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
