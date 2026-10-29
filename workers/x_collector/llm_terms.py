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
PROMPT_VERSION = "5"
KV_PREFIX = "x_qterms:"
X_QUERY_TERMS_SYSTEM = """You are an expert search query engineer for Persian Twitter/X buyer discovery.
Real Persian users on X almost never write catalog or marketplace names. They describe a problem, ask people for advice, or say what they want, in colloquial first-person Persian.

For EACH product write search phrases in exactly THREE formats. Every phrase is 2-5 words, colloquial Persian, entity-blind (no brand or product name) and must be something a real person would literally tweet:

FORMAT 1 - pain_symptoms: the person's own problem, symptom or frustration with their current situation.
   e.g. «دندونم تیر میکشه», «سنگ نما شوره زده», «لپ تاپم هنگ میکنه», «دیگه جواب نمیده», «خسته شدم از»
FORMAT 2 - advice_requests: the person asks others for help, a recommendation or experience, mentioning the topic.
   e.g. «کسی راهی بلده برای شوره سنگ», «چی پیشنهاد میدین برای درد دندون», «تجربه دارین دربارهٔ لپ تاپ استوک»
FORMAT 3 - implicit_solutions: the person wants an outcome or a solution, or states a direct need to get/find/buy something in this area.
   e.g. «راهکار رفع شوره سنگ», «روش افزایش سرعت سیستم», «دنبال دندونپزشک خوب»

QUANTITY RULE: give 5 to 8 phrases per format. Fewer than 5 finds nothing; more than 8 adds duplicates and noise. Each phrase must be clearly different in wording from the others, and none may be a slogan, generic filler («کیفیت عالی», «تخفیف ویژه») or a single generic word.

Also give:
   - names: 2-4 core product/service keywords (1-3 words, Persian or English, e.g. «سنگ نما», «لمینت», «notion ai»).
   - cues: 3-8 buying/seeking cues (1-3 words) written next to names (e.g. «بخرم», «قیمت», «پیشنهاد», «معرفی»).
   - negatives: 0-4 words typical of seller ads (e.g. «تخفیف», «کد تخفیف»).
   - model: brand + model for hardware/gadgets, else null.

Return JSON:
{"items":[{"product_id":"...","pain_symptoms":[...],"advice_requests":[...],"implicit_solutions":[...],"names":[...],"cues":[...],"negatives":[...],"model":null}]}"""
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

    def words(values: Any, lo: int, hi: int, cap: int) -> list[str]:   # Persian or Latin
        out = []
        for value in values if isinstance(values, list) else []:
            term = clean_term(value)
            if lo <= len(term.split()) <= hi and (_FA.search(term) or _LATIN.search(term)) and term not in out:
                out.append(term)
        return out[:cap]

    model = clean_term(raw.get("model") or "")
    model = model if model and model.lower() not in {"null", "none"} and len(model.split()) <= 3 and len(model) >= 3 else None

    # MP-CABS Triplets
    triplets = []
    for item in raw.get("triplets") if isinstance(raw.get("triplets"), list) else []:
        if isinstance(item, dict):
            p = clean_term(item.get("persona") or "")
            t = clean_term(item.get("trigger") or "")
            pn = clean_term(item.get("pain") or "")
            if p or t or pn:
                triplets.append({"persona": p, "trigger": t, "pain": pn})

    pain_symptoms = terms(raw.get("pain_symptoms"), 2, 5, 8)
    if not pain_symptoms and "problems" in raw:
        pain_symptoms = terms(raw.get("problems"), 2, 5, 8)

    dissatisfactions = terms(raw.get("dissatisfactions"), 2, 5, 6)
    if not dissatisfactions and "complaints" in raw:
        dissatisfactions = terms(raw.get("complaints"), 2, 5, 6)

    advice_requests = terms(raw.get("advice_requests"), 2, 6, 8)
    if not advice_requests and "alternatives" in raw:
        advice_requests = terms(raw.get("alternatives"), 2, 6, 6)

    implicit_solutions = terms(raw.get("implicit_solutions"), 2, 6, 8)

    res = {
        "triplets": triplets[:3],
        "pain_symptoms": pain_symptoms,
        "dissatisfactions": dissatisfactions,
        "advice_requests": advice_requests,
        "implicit_solutions": implicit_solutions,
        "names": words(raw.get("names"), 1, 3, 6),
        "cues": words(raw.get("cues"), 1, 3, 8),
        "negatives": words(raw.get("negatives"), 1, 3, 5),
        "model": model,
        # backward-compatibility mappings
        "problems": pain_symptoms,
        "complaints": dissatisfactions,
        "alternatives": advice_requests,
    }
    return res


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
