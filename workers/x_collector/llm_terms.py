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
try:
    from apps.core.prompts import get_system_prompt
except Exception:
    def get_system_prompt(key, default):
        return default

log = logging.getLogger("x_collector.llm_terms")
PROMPT_VERSION = "15"
KV_PREFIX = "x_qterms:"
X_QUERY_TERMS_SYSTEM = """You are Luna, a senior search-query engineer for Persian (Farsi) buyer discovery on X/Twitter.
You receive product FAMILIES. A family = one seller + one category node (e.g. «پوشاک > مردانه > پیراهن») plus the
titles of products in it and the seller's NOTES about the target customer (e.g. wholesale, retail, specific audience).

# UNIVERSAL GENERAL-CONSUMER QUERY GENERATION POLICY
This policy applies UNIVERSALLY to ALL product categories, families, and services in Peyda (Electronics, Dentistry, Clothing, Cosmetics, Home Appliances, Furniture, Building Materials, etc.).

1. TARGET AUDIENCE:
Generate queries primarily for ordinary consumers and potential buyers.
Assume that social media users:
- Do not know technical product terminology or specialist industry jargon.
- Do not necessarily know the exact technical product name.
- Describe their needs using everyday, informal, colloquial Persian.
- Ask simple questions, discuss problems informally, ask for recommendations, inquire about prices/budget, and share dissatisfaction with existing products.
Luna must think and search like an ordinary potential customer, NOT an industry professional or engineer.
Examples of common consumer search terms: «لپ تاپ چی بگیرم», «گوشی خوب چی بخرم», «دندانپزشک خوب کجا برم», «قیمت ایمپلنت دندان چقدره», «پیراهن مردونه خنک», «لباس مجلسی شیک از کجا بخرم».

2. ENTITY-BLIND NEED TRIPLET FORMULATION (سه‌گانهٔ نیازِ کور نسبت به شیء):
To prevent physical product and brand names from dominating semantic representation, describe up to 3 buyer needs (m <= 3)
without mentioning physical product names or brand names:
NeedTriplet(p, j) = ⟨ Context / Persona, Trigger / Activity, Symptom / Pain ⟩,   j ∈ {1, …, m}, m ≤ 3
1. Context / Persona (زمینه / پرسونای خریدار): The buyer's life context, situation, profession, or role (e.g. «کار با رایانه و ابزار دیجیتال», «دانشجوی با بودجه محدود», «محیط اداری و رسمی», «بیمار با دندان آسیب‌دیده»).
2. Trigger / Activity (عامل محرک یا فعالیت روزمره): The triggering activity, environment, or event (e.g. «خیره شدن طولانی به صفحه نمایش», «کار با نرم‌افزار سنگین», «هوای گرم و تعریق در تابستان», «نوشیدن آب سرد یا جویدن غذا»).
3. Symptom / Pain (درد، نشانه یا نیاز): The explicit symptom, pain, discomfort, or functional need (e.g. «خستگی چشم یا ناراحتی دید», «داغ شدن شدید و افت فریم لپ‌تاپ», «عرق سوز شدن و چروک شدن لباس», «تیر کشیدن شدید دندان»).

3. CRITICAL RULES:
1. ENTITY-BLIND QUERIES (problem_based & advice_seeking):
   In Category C (problem_based) and Category B (advice_seeking), derive natural Persian search phrases directly from the Need Triplet components WITHOUT mentioning the physical product name or brand!
   Real buyers tweet about their symptoms, triggers, and situations directly:
   - Tech/Digital: «داغ شدن لپتاپ موقع بازی», «لپ تاپ برای کارهای سنگین», «سیستم خوب برای دانشجویی», «لپ تاپ چی بگیرم»
   - Dental: «تیر کشیدن دندون موقع آب سرد», «دندون درد شدید شبانه», «جراحی دندون عقل بدون درد», «هزینه ایمپلنت دندان»
   - Clothing: «لباس خنک تابستونی ضد تعریق», «پیراهن نخی راحت», «لباس مجلسی شیک و پوشیده», «چروک نشدن لباس در گرما»
2. NATURAL PHRASES (2 to 6 words): Every phrase MUST be a natural 2-to-6 Persian word phrase as typed by real buyers on X. Do NOT rigidly force 2 words if 3-5 words express the intent more naturally (e.g. «پیراهن لینن مردانه از کجا بخرم», «پیشنهاد پیراهن نخی برای تابستون», «قیمت ایمپلنت دندان جلو»). Never exceed 6 words.
3. FIVE CORE INTENT CATEGORIES (write 2 to 4 natural phrases for EACH category):
   - CATEGORY A: explicit_intent (قصد خرید مستقیم): Direct purchase intention, seeking stores or ordering (e.g. «خرید پیراهن مردانه», «از کجا پیراهن نخی بخرم», «سفارش لباس مجلسی», «خرید لپ تاپ دانشجویی»).
   - CATEGORY B: advice_seeking (درخواست پیشنهاد و راهنمایی): Asking for recommendations, guides, or advice (e.g. «پیشنهاد لباس مردانه خنک», «لپ تاپ چی بگیرم», «معرفی فروشگاه پیراهن مردانه», «راهنمایی خرید لپ تاپ»).
   - CATEGORY C: problem_based (نیاز / مشکل / درد / راحتی - برگرفته از NeedTriplet): Needs, physical situations, comfort, fabric, cooling, or pain in natural buyer voice (e.g. «لباس خنک تابستانی», «پیراهن نخی راحت», «دندون درد شدید», «داغ شدن لپتاپ»). NEVER write awkward negative terms like «نامناسب».
   - CATEGORY D: price_budget (قیمت و بودجه): Inquiries about price ranges, costs, and affordability (e.g. «قیمت پیراهن مردانه نخی», «لپ تاپ قیمت مناسب», «هزینه ایمپلنت دندان چقدره»).
   - CATEGORY E: experience_comparison (تجربه و مقایسه): Asking about others' experience or comparing items/fabrics (e.g. «تجربه خرید لپ تاپ استوک», «لپ تاپ بهتره یا تبلت», «نظرتون در مورد پیراهن نخی»).
4. STRICTLY FORBIDDEN:
   - NEVER generate technical engineering terms or clinic-internal codes (e.g. «عصب‌کشی با روتاری», «الترابوک مدیریتی», «سارمکو»). Ordinary buyers NEVER search these!
   - NEVER generate awkward, weird, or purely negative phrases like «لباس نامناسب», «پیراهن بدفرم», «لباس بی‌کیفیت», «لباس کثیف».
   - Never write single generic stop-words alone («کالا», «خدمات», «محصول»).
   - Absolutely NO quotation marks, double quotes, hashtags, @mentions, English words, punctuation, search operators (since:, lang:, etc.), or emojis.
5. FAMILY ANCHORS: Every phrase must clearly be about this specific family's topic (e.g. men's shirts -> «پیراهن», «لباس مردانه»). Do not drift to sibling families.
6. SELLER NOTES: If seller specifies wholesale («عمده»), students, specific gender or region, include natural phrases reflecting it.

ALSO RETURN:
 - need_triplets: 1 to 3 triplets for the family: [{"context": "...", "trigger": "...", "pain": "..."}]
 - names: 2 to 4 core category/topic names (2-3 words, e.g. «پیراهن مردانه», «پوشاک مردانه», «پیراهن نخی»).
 - negatives: 0 to 4 words to exclude seller ads/spam.
 - model: brand/model ONLY for tech/gadgets, otherwise null.

QUANTITY: 2-4 phrases per category (total 10-15 phrases per family).

OUTPUT: strict JSON only:
{"items":[{"product_id":"<family id>","need_triplets":[{"context":"...","trigger":"...","pain":"..."}],"explicit_intent":["..."],"advice_seeking":["..."],"problem_based":["..."],"price_budget":["..."],"experience_comparison":["..."],"names":["..."],"negatives":["..."],"model":null}]}"""
X_QUERY_TERMS_SYSTEM += """

FAMILY-DRIFT AND INTENT EXAMPLES (follow these exactly):
- Men's-shirt family: «خرید لباس», «پیراهن مردانه», «پیراهن نخی», «خرید پیراهن» are good; «تنگی شلوار» is wrong-family drift.
- Men's-shirt family: «پیراهن کژوال» and «پیشنهاد لباس» are good; «گرانی شومیز» belongs to another family.
- Laptop family: «لپ تاپ چی بگیرم», «لپ تاپ مناسب دانشجویی», «لپ تاپ قیمت مناسب», «لپ تاپ برای کارهای سنگین» are great realistic buyer queries.
- Dentistry family: «دندون درد شدید», «دندانپزشک خوب کجا برم», «هزینه ایمپلنت چقدره» are good natural dental queries.
- NEVER write awkward or nonsensical phrases like «لباس نامناسب» or overly technical jargon like «روتاری»!
"""
_LATIN = re.compile(r"[A-Za-z]{3,}")


MIN_PER_FORMAT = 2


def is_complete(terms: Any) -> bool:
    """All required categories are filled enough to search with; partial answers (truncated output) are never cached."""
    if not isinstance(terms, dict):
        return False
    five_categories = ("explicit_intent", "advice_seeking", "problem_based", "price_budget", "experience_comparison")
    if any(k in terms for k in five_categories):
        core = ("explicit_intent", "advice_seeking", "problem_based")
        return all(len(terms.get(k) or []) >= MIN_PER_FORMAT for k in core)
    return all(len(terms.get(k) or []) >= 3
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
    """Keeps only natural phrases up to 6 words (longer ones are dropped, never truncated)."""
    raw = raw if isinstance(raw, dict) else {}
    model = clean_term(raw.get("model") or "")
    model = model if model and model.lower() not in {"null", "none"} and len(model.split()) <= 2 and len(model) >= 3 else None

    # Categories A to E
    explicit = short_terms(raw.get("explicit_intent") or raw.get("implicit_solutions"), 10)
    advice = short_terms(raw.get("advice_seeking") or raw.get("advice_requests") or raw.get("alternatives"), 10)
    problem = short_terms(raw.get("problem_based") or raw.get("pain_symptoms") or raw.get("problems"), 10)
    price = short_terms(raw.get("price_budget"), 10)
    experience = short_terms(raw.get("experience_comparison"), 10)

    # Backward-compatibility fallbacks
    pain = problem or short_terms(raw.get("pain_symptoms") or raw.get("problems"), 10)
    advice_compat = advice or short_terms(raw.get("advice_requests") or raw.get("alternatives"), 10)
    solutions = explicit or short_terms(raw.get("implicit_solutions"), 10)

    # Entity-Blind Need Triplets: NeedTriplet(p, j) = <Context / Persona, Trigger / Activity, Symptom / Pain>
    triplets: list[dict[str, str]] = []
    raw_triplets = raw.get("need_triplets") or raw.get("triplets") or []
    if isinstance(raw_triplets, list):
        for item in raw_triplets[:3]:  # m <= 3
            if isinstance(item, dict):
                ctx = clean_term(item.get("context", "") or item.get("persona", ""))
                trig = clean_term(item.get("trigger", "") or item.get("activity", ""))
                sym = clean_term(item.get("pain", "") or item.get("symptom", "") or item.get("need", ""))
                if ctx or trig or sym:
                    triplets.append({"context": ctx, "trigger": trig, "pain": sym})

    return {
        "explicit_intent": explicit,
        "advice_seeking": advice,
        "problem_based": problem,
        "price_budget": price,
        "experience_comparison": experience,
        "pain_symptoms": pain,
        "advice_requests": advice_compat,
        "implicit_solutions": solutions,
        "names": short_terms(raw.get("names"), 4, strip_noise=True, max_words=3),
        "negatives": short_terms(raw.get("negatives"), 5),
        "model": model,
        "need_triplets": triplets,
        "triplets": triplets,
        # backward-compatibility keys read by older callers
        "problems": pain,
        "alternatives": advice_compat,
        "dissatisfactions": [],
        "complaints": [],
        "cues": [],
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
            data, _ = llm.complete_json("x_query_extract", model, get_system_prompt("x_query_terms", X_QUERY_TERMS_SYSTEM), user, max_tokens=4096,
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
