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
PROMPT_VERSION = "4"
KV_PREFIX = "x_qterms:"
X_QUERY_TERMS_SYSTEM = """You are an expert search query engineer implementing the MP-CABS (Entity-Blind Need Triplet) framework for Twitter/X Persian opportunity discovery.
Unlike Instagram (where users look for explicit service/product keywords and hashtags), real Persian users on Twitter/X NEVER tweet catalog or marketplace names (like "سنگ اسلب چینی ازنا", "تراورتن آبگرم", "لپ تاپ تینکپد تی ۴۸۰").
Instead, Twitter users post about their personal symptoms, situational triggers, and daily pains, or ask for informal advice or implicit solutions WITHOUT naming the product.

For each product provided, generate:
1. triplets: 1-3 Entity-Blind Need Triplets:
   [{"persona": "...", "trigger": "...", "pain": "..."}]
   - persona: The person's context/role (e.g. "سازنده ساختمان / معمار", "برنامه‌نویس / گرافیست", "داوطلب کنکور", "بیمار با دندان درد"). NO product or brand name!
   - trigger: The triggering situation/activity (e.g. "سرما و یخبندان زمستان", "کامپایل و رندر سنگین", "نزدیک شدن آزمون آزمایشی", "غذا خوردن و آب سرد").
   - pain: The pain, symptom, or friction (e.g. "شوره زدن و ترک خوردن سنگ نما", "هنگ کردن سیستم و پر شدن رم", "افت تراز و فراموشی زیست", "تیر کشیدن شدید دندان").

2. The 4 MP-CABS Query Angles:
   - pain_symptoms: 3-6 short first-person conversational symptom phrases (2-4 words each) in colloquial Persian describing the physical/practical problem.
     (e.g. «دندونم تیر میکشه», «لپ تاپم بالا نمیاد», «سنگ نما شوره زده», «ترک خوردن سنگ کف», «ترازم افت کرده»).
   - dissatisfactions: 2-4 colloquial phrases expressing frustration with current setup or workarounds (2-4 words each).
     (e.g. «دیگه جواب نمیده», «خسته شدم از تمیز کردن», «این لپ تاپ دیگه نمیکشه», «این مسکن هم جواب نمیده»).
   - advice_requests: 3-5 informal colloquial advice questions without naming the product (2-5 words each).
     (e.g. «کسی راهی بلده برای», «چی پیشنهاد میدین برای», «تجربه دارین برای حل», «برای این مشکل چیکار کنم»).
   - implicit_solutions: 2-4 implicit outcome-oriented solution phrases (2-4 words each).
     (e.g. «راهکار رفع شوره», «جلوگیری از ترک در سرما», «روش افزایش سرعت سیستم», «تسکین سریع دندان درد»).

3. Secondary / Core terms:
   - names: 2-4 minimal essential core product/service keywords (Persian and English, 1-3 words each, e.g. «سنگ نما», «سنگ تراورتن», «دندانپزشکی», «لمینت»). NEVER marketing slogans, never cosmetic fluff (NO «طرح لبخند», «کیفیت برتر», «تخفیف ویژه»).
   - cues: 3-8 buying/seeking cues (1-3 words) buyers write next to names (e.g. «بخرم», «قیمت», «پیشنهاد», «دکتر خوب», «معرفی»).
   - negatives: 0-4 words that appear in seller ads (e.g. «تخفیف», «کد تخفیف», «فروش ویژه»).
   - model: brand + model if hardware/gadget (e.g. "ThinkPad T480"), else null.

Return JSON:
{"items":[{"product_id":"...","triplets":[{"persona":"...","trigger":"...","pain":"..."}],"pain_symptoms":[...],"dissatisfactions":[...],"advice_requests":[...],"implicit_solutions":[...],"names":[...],"cues":[...],"negatives":[...],"model":null}]}"""
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

    advice_requests = terms(raw.get("advice_requests"), 2, 6, 6)
    if not advice_requests and "alternatives" in raw:
        advice_requests = terms(raw.get("alternatives"), 2, 6, 6)

    implicit_solutions = terms(raw.get("implicit_solutions"), 2, 5, 6)

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
