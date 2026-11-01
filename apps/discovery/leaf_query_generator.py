"""Leaf Category Query Generation & Replacement Engine for Peyda v2.

Generates platform-specific query sets strictly at the active Leaf Category level:
- X: Three semantic domains (Pain & Symptoms, Purchase Intent & Advice, Persona/Context/Lifestyle),
  colloquial Persian (2-6 words), MMR diversified, maximum 10 queries per leaf category.
- Instagram: Product/service-focused concise queries (1-3 words), MMR diversified,
  maximum 5 queries per leaf category.

Provides idempotent, dry-run capable query replacement that archives obsolete active queries,
initializes fresh v2 Bayesian baselines, and preserves all historical execution data.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

from django.db import transaction

from apps.businesses.models import Business
from apps.products.models import Category, Product
from apps.products.taxonomy import get_active_leaf_categories
from shared.mmr import select_by_mmr
from need_engine.text import norm

logger = logging.getLogger("discovery.leaf_query_generator")

MAX_X_QUERIES_PER_LEAF = 10
MAX_IG_QUERIES_PER_LEAF = 5

X_THREE_DOMAINS_SYSTEM = """You are Luna, a senior Persian search intelligence specialist generating high-converting buyer discovery queries for X (Twitter).
You receive ONE Leaf Category (a category with no sub-categories) and all active seller products associated with it.

# UNIVERSAL GENERAL-CONSUMER QUERY GENERATION POLICY
This policy applies UNIVERSALLY to ALL product categories, families, and services in Peyda (Clothing, Dentistry, Cosmetics & Skincare, Home Appliances, Building Materials, Furniture, Electronics, etc.).

1. TARGET AUDIENCE:
Generate queries primarily for ordinary consumers and potential buyers.
Assume that social media users:
- Do not know technical product terminology or specialist industry jargon.
- Do not necessarily know the exact technical product name.
- Describe their needs using everyday, informal, colloquial Persian.
- Ask simple questions, discuss problems informally, ask for recommendations, inquire about prices/budget, and share dissatisfaction with existing products.
Luna must think and search like an ordinary potential customer, NOT an industry professional or engineer.

2. GENERATE QUERIES ACROSS EXACTLY THREE PRIMARY SEMANTIC DOMAINS:
1. DOMAIN A: pain_symptoms (نیازها، دردها، علائم، مشکلات و نارضایتی روزمره)
   Everyday problems, dissatisfaction, symptoms, or discomfort that the seller's product/service directly solves.
   Examples across categories:
   - Electronics: «لپ تاپم کند شده», «باتری گوشی زود خالی میشه», «چشمم از کار با مانیتور میسوزه»
   - Dentistry: «دندون درد دارم چیکار کنم», «دندونام نامرتبه چیکار کنم», «تیر کشیدن دندون موقع آب سرد»
   - Clothing: «لباس مناسب مهمونی ندارم», «لباس خنک تابستونی ضد تعریق», «چروک نشدن لباس در گرما»
   - Cosmetics: «پوستم خیلی خشکه», «برای جوش صورتم چی خوبه», «دنبال کرم مرطوب کننده هستم»
   - Home Appliances: «یخچالم درست خنک نمیکنه», «ماشین لباسشویی صدا میده»
   (2 to 4 queries)

2. DOMAIN B: purchase_advice (قصد خرید مستقیم، درخواست پیشنهاد و استعلام قیمت)
   Expressing direct purchase intent, asking for recommendations, asking where to buy, comparing options, asking about price or budget.
   Examples across categories:
   - Electronics: «گوشی خوب چی بخرم», «مانیتور مناسب کار چی بخرم», «لپ تاپ چی بگیرم»
   - Dentistry: «دندانپزشک خوب کجا میشناسید», «برای ایمپلنت کجا برم», «قیمت ایمپلنت چقدره»
   - Clothing: «پیراهن مردونه میخوام», «شلوار خوب از کجا بخرم», «لباس زیر زنانه خوب چی بگیرم»
   - Cosmetics: «کرم ضدآفتاب خوب چی بخرم», «ضد آفتاب قیمت مناسب چی پیشنهاد میدید»
   - Home Appliances: «ماشین لباسشویی خوب چی بخرم», «جاروبرقی قوی چی پیشنهاد میدید»
   - Furniture: «مبل راحتی از کجا بخرم», «دنبال میز ناهارخوری شیکم», «مبل خوب و قیمت مناسب»
   - Building Materials: «سنگ مناسب نمای ساختمان چی بخرم», «قیمت سنگ ساختمون چنده»
   (3 to 5 queries)

3. DOMAIN C: persona_context (پرسونا، موقعیت، سبک زندگی و شرایط استفاده)
   Needs derived from lifestyle, use-cases, occupations, living spaces, or daily contexts.
   Examples across categories:
   - Electronics: «لپ تاپ مناسب دانشجو», «هدفون برای استفاده روزانه»
   - Clothing: «لباس مناسب مهمونی چی بخرم», «پیراهن مناسب استایل رسمی», «لباس مناسب باشگاه»
   - Furniture: «برای خونه کوچیک چه مبلی خوبه»
   - Building Materials: «برای نمای خونه چه سنگی خوبه», «سنگ کف حیاط چی پیشنهاد میدید»
   (2 to 4 queries)

3. CRITICAL RULES:
- Natural, conversational Persian (2 to 6 words).
- General consumer language with clear relevance: every query must still have a meaningful connection to the Leaf Category and seller's products (do NOT generate vague queries like «چی بخرم» without product context).
- Strictly avoid unnecessary technical specifications, engineering codes, or specialized jargon unless the product is inherently specialized and ordinary buyers use that term.
- No search operators (since:, lang:, etc.), no hashtags (#), no emojis, no quotes, no English text.
- Return between 8 and 12 candidate queries total.

Strict JSON format:
{
  "leaf_category_id": <int>,
  "queries": [
    {"query": "...", "domain": "pain_symptoms", "related_products": ["..."]},
    {"query": "...", "domain": "purchase_advice", "related_products": ["..."]},
    {"query": "...", "domain": "persona_context", "related_products": ["..."]}
  ]
}
"""

INSTAGRAM_FIVE_QUERIES_SYSTEM = """You are Luna, an Instagram e-commerce search specialist.
You receive ONE Leaf Category and the seller's active products associated with it.

# UNIVERSAL GENERAL-CONSUMER QUERY GENERATION POLICY (INSTAGRAM)
Your task is to generate concise, highly searchable product and service terms for Instagram search.
Unlike X, Instagram queries must be SHORT, DIRECT, and PRODUCT/SERVICE-ORIENTED (1 to 3 Persian words).
Focus on the common terms everyday consumers type in Instagram search when looking for products or service providers.
NEVER generate long conversational questions or sentences.

Use this priority order:
1. exact_category: Main category or service name used by consumers (e.g. «پیراهن مردانه», «ایمپلنت دندان», «کرم مرطوب کننده», «مبل راحتی», «سنگ ساختمانی»)
2. exact_product: Popular consumer product name from the catalog (e.g. «پیراهن لینن», «ایمپلنت», «ضدآفتاب بی رنگ», «میز ناهارخوری»)
3. synonym: Common colloquial synonym or alternative consumer phrasing (e.g. «پیراهن مردونه», «کاشت دندان», «کرم ضد افتاب», «مبل راحتی ال»)
4. variation: Key consumer variation, style, or application (e.g. «پیراهن اسپرت», «کلینیک ایمپلنت», «سنگ نما», «لباس راحتی»)
5. alternative_spelling: Widely searched related term or alternative spelling (e.g. «پیراهن نخی», «ایمپلنت فوری», «مبل مدرن»)

RULES:
- 1 to 3 Persian words per query.
- Short, natural, and directly searchable by ordinary consumers.
- No conversational questions (NO «چی بخرم», NO «از کجا بخرم»).
- No hashtags (#), no emojis, no English words.
- Return 5 to 8 candidate queries.

Strict JSON format:
{
  "leaf_category_id": <int>,
  "queries": [
    {"query": "...", "query_type": "exact_category", "related_products": ["..."]},
    {"query": "...", "query_type": "exact_product", "related_products": ["..."]},
    {"query": "...", "query_type": "synonym", "related_products": ["..."]},
    {"query": "...", "query_type": "variation", "related_products": ["..."]},
    {"query": "...", "query_type": "alternative_spelling", "related_products": ["..."]}
  ]
}
"""


def _clean_query_text(text: str) -> str:
    cleaned = re.sub(r"[#@«»\"'\n\r\t?؟!,،.:;()]", " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()


def generate_leaf_category_x_queries(
    leaf_category: Category,
    products: list[Product],
    business: Business,
    llm_func: Callable | None = None,
) -> list[dict[str, Any]]:
    """
    Generates diversified X search queries for one leaf category across the three domains.
    Applies MMR and caps at MAX_X_QUERIES_PER_LEAF (10).
    """
    if llm_func is None:
        from apps.core.llm import chat_json as llm_func

    prod_names = [p.name for p in products]
    user_prompt = (
        f"Seller: {business.name}\n"
        f"Leaf Category ID: {leaf_category.id}\n"
        f"Leaf Category Path: {leaf_category.get_full_path()}\n"
        f"Associated Active Products:\n"
        + "\n".join(f"- {p.name}: {(p.description or '')[:100]}" for p in products[:15])
    )

    candidates: list[dict[str, Any]] = []
    try:
        data = llm_func(X_THREE_DOMAINS_SYSTEM, user_prompt, purpose="x_leaf_queries", business_id=business.id)
        raw_items = data.get("queries", []) if isinstance(data, dict) else []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            q_text = _clean_query_text(item.get("query", ""))
            domain = str(item.get("domain", "")).strip().lower()
            if domain not in {"pain_symptoms", "purchase_advice", "persona_context"}:
                domain = "purchase_advice"
            words = q_text.split()
            if 2 <= len(words) <= 7 and q_text:
                candidates.append({
                    "query": q_text,
                    "domain": domain,
                    "related_products": [str(x) for x in item.get("related_products", []) if str(x) in prod_names] or prod_names[:3],
                })
    except Exception as exc:
        logger.warning("LLM generation of X queries failed for leaf category %s: %s", leaf_category.id, exc)

    # Deterministic fallback if LLM provided insufficient candidates
    if len(candidates) < 4:
        cat_words = leaf_category.name.split()
        cat_short = " ".join(cat_words[:3]) if len(cat_words) >= 3 else leaf_category.name
        raw_prod = prod_names[0] if prod_names else cat_short
        p_words = raw_prod.split()
        prod_short = " ".join(p_words[:3]) if len(p_words) >= 3 else raw_prod

        fallback_queries = [
            # Domain A: Pain & Symptoms
            {"query": f"مشکل {prod_short}", "domain": "pain_symptoms"},
            {"query": f"درمان {cat_short}", "domain": "pain_symptoms"},
            # Domain B: Purchase Intent & Advice
            {"query": f"{prod_short} چی بخرم", "domain": "purchase_advice"},
            {"query": f"خرید {cat_short} قیمت مناسب", "domain": "purchase_advice"},
            {"query": f"پیشنهاد بهترین {prod_short}", "domain": "purchase_advice"},
            {"query": f"از کجا {prod_short} بخرم", "domain": "purchase_advice"},
            # Domain C: Persona & Context
            {"query": f"{prod_short} مناسب روزمره", "domain": "persona_context"},
            {"query": f"{cat_short} باکیفیت و عالی", "domain": "persona_context"},
        ]
        seen_q = {_clean_query_text(c["query"]).casefold() for c in candidates}
        for fb in fallback_queries:
            q_clean = _clean_query_text(fb["query"])
            words = q_clean.split()
            if 2 <= len(words) <= 6 and q_clean.casefold() not in seen_q:
                seen_q.add(q_clean.casefold())
                candidates.append({
                    "query": q_clean,
                    "domain": fb["domain"],
                    "related_products": prod_names[:3],
                })

    # Apply MMR Diversification independently for X
    reference = f"{leaf_category.get_full_path()} {' '.join(prod_names)}"
    selected = select_by_mmr(
        candidates,
        query_text_fn=lambda item: item["query"],
        reference_context=reference,
        top_k=min(len(candidates), MAX_X_QUERIES_PER_LEAF),
        lambda_param=0.68,
    )
    return selected[:MAX_X_QUERIES_PER_LEAF]


def generate_leaf_category_instagram_queries(
    leaf_category: Category,
    products: list[Product],
    business: Business,
    llm_func: Callable | None = None,
) -> list[dict[str, Any]]:
    """
    Generates concise product/service Instagram search queries for one leaf category.
    Strictly 1-3 words, MMR diversified, capped at MAX_IG_QUERIES_PER_LEAF (5).
    """
    if llm_func is None:
        from apps.core.llm import chat_json as llm_func

    prod_names = [p.name for p in products]
    user_prompt = (
        f"Seller: {business.name}\n"
        f"Leaf Category: {leaf_category.name} ({leaf_category.get_full_path()})\n"
        f"Associated Products: {', '.join(prod_names[:10])}\n"
    )

    candidates: list[dict[str, Any]] = []
    try:
        data = llm_func(INSTAGRAM_FIVE_QUERIES_SYSTEM, user_prompt, purpose="ig_leaf_queries", business_id=business.id)
        raw_items = data.get("queries", []) if isinstance(data, dict) else []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            q_text = _clean_query_text(item.get("query", ""))
            q_type = str(item.get("query_type", "exact_product")).strip().lower()
            words = q_text.split()
            # Must be concise: 1 to 3 words
            if 1 <= len(words) <= 3 and q_text:
                candidates.append({
                    "query": q_text,
                    "query_type": q_type,
                    "related_products": [str(x) for x in item.get("related_products", []) if str(x) in prod_names] or prod_names[:3],
                })
    except Exception as exc:
        logger.warning("LLM generation of Instagram queries failed for leaf category %s: %s", leaf_category.id, exc)

    # Deterministic fallback if insufficient candidates
    if len(candidates) < 3:
        cat_name = leaf_category.name
        seen_q = {_clean_query_text(c["query"]).casefold() for c in candidates}
        cat_words = cat_name.split()
        short_cat = " ".join(cat_words[:2]) if len(cat_words) >= 2 else cat_name
        deterministic = [
            {"query": short_cat, "query_type": "exact_category"},
        ]
        if len(cat_words) >= 3:
            deterministic.append({"query": " ".join(cat_words[:3]), "query_type": "exact_category"})

        for p in products[:4]:
            p_words = p.name.split()
            short_p = " ".join(p_words[:2]) if len(p_words) >= 2 else p.name
            deterministic.append({"query": short_p, "query_type": "exact_product"})
            if len(p_words) >= 3:
                deterministic.append({"query": " ".join(p_words[:3]), "query_type": "variation"})

        # Add keyword variants
        for kw in leaf_category.keywords or []:
            kw_clean = _clean_query_text(str(kw))
            kw_words = kw_clean.split()
            if 1 <= len(kw_words) <= 3:
                deterministic.append({"query": kw_clean, "query_type": "synonym"})

        for item in deterministic:
            q_clean = _clean_query_text(item["query"])
            words = q_clean.split()
            if 1 <= len(words) <= 3 and q_clean.casefold() not in seen_q:
                seen_q.add(q_clean.casefold())
                candidates.append({
                    "query": q_clean,
                    "query_type": item["query_type"],
                    "related_products": prod_names[:3],
                })

    # Apply MMR Diversification independently for Instagram
    reference = f"{leaf_category.name} {' '.join(prod_names)}"
    selected = select_by_mmr(
        candidates,
        query_text_fn=lambda item: item["query"],
        reference_context=reference,
        top_k=min(len(candidates), MAX_IG_QUERIES_PER_LEAF),
        lambda_param=0.60,
    )
    return selected[:MAX_IG_QUERIES_PER_LEAF]


def replace_platform_queries_for_business(
    business: Business,
    dry_run: bool = False,
    llm_func: Callable | None = None,
    platform: str = "all",
) -> dict[str, Any]:
    """
    Idempotent and safe replacement of active queries for a business/tenant.
    - Operates strictly on active leaf categories (categories with active products and no children).
    - Generates & validates the v2 query set first.
    - If generation or validation fails, preserves the existing active queries.
    - Archives previous active queries and creates v2 queries + fresh Bayesian baselines.
    - Preserves all historical execution records, posts, and opportunities.
    """
    from apps.discovery.models import (
        InstagramCategoryTarget,
        InstagramSourceStat,
        XSearchQuery,
    )

    leaf_categories = get_active_leaf_categories(business)
    report: dict[str, Any] = {
        "business_id": business.id,
        "business_name": business.name,
        "leaf_categories_count": len(leaf_categories),
        "dry_run": dry_run,
        "leaf_categories": [],
        "errors": [],
    }

    if not leaf_categories:
        report["message"] = "هیچ دسته برگ فعالی با محصول فعال برای این کسب‌وکار یافت نشد."
        return report

    process_x = platform.lower() in ("all", "x")
    process_ig = platform.lower() in ("all", "instagram", "ig")

    # Step 1: Pre-generate and validate all leaf queries in-memory
    planned_x_by_cat: dict[int, list[dict[str, Any]]] = {}
    planned_ig_by_cat: dict[int, list[dict[str, Any]]] = {}

    for leaf in leaf_categories:
        prods = list(leaf.products.filter(business=business, status="ACTIVE"))
        cat_info = {
            "category_id": leaf.id,
            "category_name": leaf.name,
            "category_path": leaf.get_full_path(),
            "category_depth": leaf.depth,
            "products_count": len(prods),
            "x_queries": [],
            "ig_queries": [],
        }

        if process_x:
            x_queries = generate_leaf_category_x_queries(leaf, prods, business, llm_func=llm_func)
            if not x_queries:
                err = f"تولید کوئری X برای دسته برگ {leaf.name} ناموفق بود."
                report["errors"].append(err)
                return report
            planned_x_by_cat[leaf.id] = x_queries
            cat_info["x_queries"] = [q["query"] for q in x_queries]

        if process_ig:
            ig_queries = generate_leaf_category_instagram_queries(leaf, prods, business, llm_func=llm_func)
            if not ig_queries:
                err = f"تولید کوئری اینستاگرام برای دسته برگ {leaf.name} ناموفق بود."
                report["errors"].append(err)
                return report
            planned_ig_by_cat[leaf.id] = ig_queries
            cat_info["ig_queries"] = [q["query"] for q in ig_queries]

        report["leaf_categories"].append(cat_info)

    if dry_run:
        report["status"] = "dry_run_success"
        report["message"] = "پیش‌نمایش کوئری‌های جدید با موفقیت آماده شد (بدون اعمال در پایگاه داده)."
        return report

    # Step 2: Atomic and safe database update
    with transaction.atomic():
        # --- Handle X Queries Replacement ---
        if process_x:
            # 1. Archive previous active X queries for this business
            XSearchQuery.objects.filter(
                business=business,
                state="active",
            ).update(
                state="archived",
                state_reason="archived_by_v2_regeneration",
            )

            # 2. Register new X queries with leaf_category and generation_version="v2"
            for leaf in leaf_categories:
                prods = list(leaf.products.filter(business=business, status="ACTIVE"))
                primary_prod = prods[0] if prods else None
                for q_item in planned_x_by_cat.get(leaf.id, []):
                    q_text = q_item["query"]
                    norm_text = norm(q_text)
                    domain = q_item.get("domain", "purchase_advice")

                    # Handle unique constraint safely: update or create
                    XSearchQuery.objects.update_or_create(
                        business=business,
                        normalized_text=norm_text,
                        defaults={
                            "text": q_text,
                            "product": primary_prod,
                            "category": leaf,
                            "kind": "auto_intent",
                            "state": "active",
                            "domain": domain,
                            "generation_version": "v2",
                            "state_reason": "active_v2_leaf_query",
                        },
                    )

        # --- Handle Instagram Queries Replacement ---
        if process_ig:
            for leaf in leaf_categories:
                ig_items = planned_ig_by_cat.get(leaf.id, [])
                new_terms = [item["query"] for item in ig_items]

                # 1. Update or create InstagramCategoryTarget
                target, _ = InstagramCategoryTarget.objects.get_or_create(
                    business=business,
                    category=leaf,
                    defaults={"is_active": True, "generation_version": "v2"},
                )
                # Archive existing terms if any
                old_terms = list(target.terms or [])
                all_archived = list(target.archived_terms or [])
                for t in old_terms:
                    if t not in all_archived and t not in new_terms:
                        all_archived.append(t)

                target.archived_terms = all_archived
                target.terms = new_terms
                target.is_active = True
                target.generation_version = "v2"
                target.save()

                # 2. Initialize new v2 Bayesian baseline for each term in InstagramSourceStat
                family_key = f"leaf_{leaf.id}"
                for term in new_terms:
                    InstagramSourceStat.objects.update_or_create(
                        business=business,
                        family_id=family_key,
                        source_type="keyword",
                        label=term[:100],
                        defaults={
                            "requests": 0,
                            "leads": 0,
                            "alpha": 1.0,
                            "beta": 1.0,
                            "generation_version": "v2",
                        },
                    )

    report["status"] = "success"
    report["message"] = "کوئری‌های جدید نسخه ۲ با موفقیت ذخیره و جایگزین کوئری‌های فعال قبلی شدند."
    return report
