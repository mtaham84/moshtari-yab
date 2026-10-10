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

from workers.x_collector.intent_queries import _FA, clean_term

log = logging.getLogger("x_collector.llm_terms")
PROMPT_VERSION = "2"
KV_PREFIX = "x_qterms:"
X_QUERY_TERMS_SYSTEM = """You write X (Twitter) search terms that find Persian posts of people who want to buy ONE specific product
(or ask where to get it, ask for a recommendation, or ask a seller «موجود دارین؟» under an ad). X matches words literally:
a query is (any name) AND (any cue). Return for each product:
- names: 3-8 ways ordinary Iranians write this product on X, 1-3 words each: Persian, English and Finglish spellings
  (e.g. «نوشن», «notion», «notion ai», «اکانت نوشن»; «هندزفری», «ایرفون»). Include the brand when people search the
  product by brand. Each name must be specific to this product: never a generic word alone (اشتراک، اکانت، خرید، قیمت).
- cues: 4-10 short words/phrases (1-3 words) a BUYER writes next to the name, specific to this kind of product
  (subscriptions: «پرمیوم», «پولی», «اکانت», «موجود دارین», «از کجا», «بخرم»; physical goods: «بخرم», «چی بگیرم»,
  «پیشنهاد», «کدوم بهتره»). No words that sellers' ads use.
- negatives: 0-5 words that appear in SELLERS' ads for this product and that a buyer would not write
  (e.g. «تخفیف», «کد تخفیف», «فعالسازی فوری», «تحویل آنی»).
- problems: 2-5 short phrases (2-4 words) a person literally writes when they have the problem this product solves,
  colloquial first person. Only problems this product really solves.
- model: brand + model the way people write it (e.g. "Airfly M7"), or null.
Return JSON: {"items":[{"product_id":"...","names":[...],"cues":[...],"negatives":[...],"problems":[...],"model":null}]}"""
_LATIN = re.compile(r"[A-Za-z]{3,}")


def _key(product: Any, card: Any) -> str:
    payload = [PROMPT_VERSION, product.content_hash(), card.model_dump(mode="json") if card is not None else None]
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def sanitize(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}

    def terms(values: Any, lo: int, hi: int, cap: int) -> list[str]:
        out = []
        for value in values if isinstance(values, list) else []:
            term = clean_term(value)
            if lo <= len(term.split()) <= hi and _FA.search(term) and term not in out:
                out.append(term)
        return out[:cap]

    model = clean_term(raw.get("model") or "")
    model = model if model and model.lower() not in {"null", "none"} and len(model.split()) <= 3 and len(model) >= 3 else None
    def words(values: Any, lo: int, hi: int, cap: int) -> list[str]:   # Persian or Latin
        out = []
        for value in values if isinstance(values, list) else []:
            term = clean_term(value)
            if lo <= len(term.split()) <= hi and (_FA.search(term) or _LATIN.search(term)) and term not in out:
                out.append(term)
        return out[:cap]

    return {"names": words(raw.get("names"), 1, 3, 8), "cues": words(raw.get("cues"), 1, 3, 10),
            "negatives": words(raw.get("negatives"), 1, 3, 5), "problems": terms(raw.get("problems"), 2, 5, 5),
            "model": model}


def _user(product: Any, card: Any) -> dict[str, Any]:
    item = {"product_id": product.product_id, "title": product.title, "type": product.product_type,
            "category": product.category_path, "description": (product.description or "")[:400]}
    if card is not None:
        item.update(what_it_is=card.what_it_is, aliases=card.aliases, problems_solved=card.problems_solved)
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
        if isinstance(hit, dict) and hit.get("h") == key:
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
            store.set(KV_PREFIX + product.product_id, {"h": key, "terms": terms})
            out[product.product_id] = terms
    return out
