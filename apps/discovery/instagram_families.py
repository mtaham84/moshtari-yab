"""Cached per-family Instagram plans, source sampling and the cheap comment funnel."""
from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone
from need_engine.text import norm

from workers.x_collector.families import build_families

INSTAGRAM_PLAN_MAX_FAMILIES_PER_TENANT = max(1, int(os.getenv("INSTAGRAM_PLAN_MAX_FAMILIES_PER_TENANT", "8")))
INSTAGRAM_PAGE_MIN_COMMENTS = max(1, int(os.getenv("INSTAGRAM_PAGE_MIN_COMMENTS", "10")))
INSTAGRAM_MAX_SOURCES_PER_TENANT_CYCLE = max(1, int(os.getenv("INSTAGRAM_MAX_SOURCES_PER_TENANT_CYCLE", "4")))
INSTAGRAM_DAILY_REQUEST_BUDGET = max(1, int(os.getenv("INSTAGRAM_DAILY_REQUEST_BUDGET", "150")))
PLAN_SYSTEM = """You are a senior Instagram discovery strategist for Persian-speaking (Iran) sellers. A FAMILY is one seller + one category
node (e.g. «پوشاک > مردانه > پیراهن») with the titles of his products and his own NOTES about the customers he wants and how
he sells. Plan where REAL BUYERS of this family can be found on Instagram. Buyers rarely use hashtags: they write
«قیمت؟», «موجوده؟», «پیام بدید», «سایز؟» in the comments under posts of sellers and niche accounts. So plan three source types.

1) hashtags (6-10): Persian or finglish tags a buyer-oriented post about this family would carry, WITHOUT the # sign.
   Product-type or common-use level (1-3 words joined by _): «لباس_مجلسی_مردانه», «جوراب_ورزشی», «لباس_خنک», «ایمپلنت_دندان».
   Mid-sized niche tags only: never mega tags («لباس», «فروشگاه», «تخفیف», «پوشاک») and never ultra-narrow ones
   (colours, sizes, model numbers, brands, city names unless the notes demand a city). No spam tags (follow4follow...).
2) keywords (4-8): 1-3 word colloquial Persian phrases for Instagram text search. Prefer pain, fit, comfort and common use
   over exact product/SKU names: «لباس تنگ», «لباس خنک», «لباس راحت», «پیراهن کژوال», «دندون درد», «ارتودنسی», «ایمپلنت».
   NEVER use purchase, price, discount, shipping, location or where-to-buy wording («خرید پیراهن», «قیمت», «تخفیف»,
   «ارسال رایگان», «کجا ایمپلنت»).
3) page_search_terms (4-8): short category and seller-type phrases to FIND relevant business accounts. Keep them broad enough
   for real account search; avoid model numbers, prices, discounts, shipping or where-to-buy questions. NEVER output
   Instagram handles or URLs: they will be verified by code.
Also return buyer_signals (6-12): short Persian phrases that appear in buyer comments for this family
(«قیمت», «موجود», «سایز», «عمده», «دایرکت»). And negative_signals (0-8): words that mark spam/sellers/irrelevance.
SELLER NOTES ARE BINDING: wholesale -> emphasise buyer shops and B2B words («عمده», «فروشگاه», «همکاری»); students/clients ->
consumer words; a city/price segment -> reflect it. Stay inside THIS family; do not drift to neighbouring categories.
Return strict JSON:
{"hashtags":[...],"keywords":[...],"page_search_terms":[...],"buyer_signals":[...],"negative_signals":[...]}"""

_HANDLE_OR_URL = re.compile(r"(?:^\s*@|instagram\.com|https?://|www\.)", re.I)
_BAD_TERM_CHARS = re.compile(r"[\n\r,،;؛!?؟@#]" )
_WEAK_SEARCH_WORDS = {norm(w) for w in (
    "خرید", "قیمت", "سفارش", "تخفیف", "ارسال", "رایگان", "کجا", "پیشنهاد", "معرفی", "تجربه", "کدام", "کدوم",
)}
GENERIC_BUYER_SIGNALS = (
    "قیمت", "موجود", "چنده", "سایز", "رنگ", "سفارش", "خرید", "عمده", "دایرکت", "پیام بدید", "ارسال دارید",
)


@dataclass(frozen=True)
class DiscoverySource:
    family_id: str
    source_type: str
    label: str
    manual: bool = False
    page: Any = None


def _phrases(value: Any, *, limit: int, words_max: int = 3, max_len: int = 60) -> list[str]:
    if not isinstance(value, list):
        return []
    out, seen = [], set()
    for raw in value:
        item = re.sub(r"\s+", " ", str(raw or "")).strip().lstrip("#")
        if not item or len(item) > max_len or len(item.split()) > words_max or _HANDLE_OR_URL.search(item) or _BAD_TERM_CHARS.search(item):
            continue
        key = item.casefold()
        if key not in seen:
            seen.add(key)
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _validate_plan(data: Any, family=None) -> dict[str, list[str]]:
    if not isinstance(data, dict):
        raise ValueError("پاسخ طرح اینستاگرام معتبر نیست")
    fields = {
        "hashtags": (10, 3, 40), "keywords": (8, 3, 60), "page_search_terms": (8, 3, 60),
        "buyer_signals": (12, 4, 50), "negative_signals": (8, 3, 50),
    }
    plan = {key: _phrases(data.get(key), limit=limit, words_max=words, max_len=max_len)
            for key, (limit, words, max_len) in fields.items()}
    for key in ("keywords", "page_search_terms"):
        plan[key] = [phrase for phrase in plan[key]
                     if not _WEAK_SEARCH_WORDS.intersection(norm(phrase).split())]
    mega = {"لباس", "فروشگاه", "تخفیف", "پوشاک", "shop", "store", "fashion"}
    plan["hashtags"] = [tag for tag in plan["hashtags"] if " " not in tag and tag.casefold() not in mega]

    # Backfill buyer_signals if below 6
    if len(plan["buyer_signals"]) < 6:
        for sig in GENERIC_BUYER_SIGNALS:
            if sig not in plan["buyer_signals"]:
                plan["buyer_signals"].append(sig)
            if len(plan["buyer_signals"]) >= 6:
                break

    # Backfill hashtags if below 6 from family context
    if family and len(plan["hashtags"]) < 6:
        fam_words = [re.sub(r"\s+", "_", t.strip()) for t in (getattr(family, "titles", []) or [])]
        for fw in fam_words:
            clean = re.sub(r"[^\w_]", "", fw)
            if clean and clean not in mega and clean not in plan["hashtags"] and len(clean) >= 3:
                plan["hashtags"].append(clean)
            if len(plan["hashtags"]) >= 6:
                break

    # Backfill keywords if below 4 from family context
    if family and len(plan["keywords"]) < 4:
        for t in (getattr(family, "titles", []) or []):
            clean = " ".join([w for w in t.split() if norm(w) not in _WEAK_SEARCH_WORDS and len(w) > 2][:3])
            if clean and clean not in plan["keywords"]:
                plan["keywords"].append(clean)
            if len(plan["keywords"]) >= 4:
                break

    # Backfill page_search_terms if below 4
    if family and len(plan["page_search_terms"]) < 4:
        for t in (getattr(family, "titles", []) or []) + [getattr(family, "title", "")]:
            cand = f"فروشگاه {t}"[:40]
            if cand not in plan["page_search_terms"]:
                plan["page_search_terms"].append(cand)
            if len(plan["page_search_terms"]) >= 4:
                break

    return plan


MAX_INSTAGRAM_TENANT_QUERIES = 50

DANGLING_CHARS_WORDS = {
    "و", "با", "در", "از", "به", "برای", "تا", "یک‌روزه", "بدون", "دارای",
    "های", "تخصصی", "کادکام", "emax", "e-max", "e max", "نانو", "فلزی",
}

DENTAL_BROAD_DISCOVERY_QUERIES = [
    # واژه‌های روال و عمومی دندانپزشکی (Routine & General)
    "دندانپزشکی", "دندونپزشکی", "دندان پزشکی", "دندون پزشکی",
    "دندانپزشک", "دندونپزشک", "دندان پزشک", "دندون پزشک",
    "کلینیک دندانپزشکی", "کلینیک دندان پزشکی", "مطب دندانپزشکی", "کلینیک دندان",
    "دندانپزشک خوب", "دندونپزشک خوب", "دندانپزشک اقساطی", "دندانپزشکی اقساطی", "دندانپزشکی ارزان", "دندانپزشکی شبانه روزی",
    "دندونم", "دندونام", "دندانم",
    # ۱. کامپوزیت (Composite)
    "کامپوزیت", "کامپوزیت دندان", "کامپوزیت ونیر", "ونیر کامپوزیت", "ترمیم کامپوزیت", "قیمت کامپوزیت", "کامپوزیت اقساطی", "کامپوزیت دندون",
    # ۲. لمینت (Laminate)
    "لمینت", "لمینت دندان", "لمینت سرامیکی", "ونیر لمینت", "قیمت لمینت", "لمینت اقساطی", "لمینت دندون",
    # ۳. ایمپلنت (Implant)
    "ایمپلنت", "ایمپلنت دندان", "کاشت دندان", "ایمپلنت فوری", "ایمپلنت یک‌روزه", "ایمپلنت اقساطی", "قیمت ایمپلنت", "ایمپلنت دندون",
    # ۴. زیبایی دندان (Cosmetic Dentistry)
    "زیبایی دندان", "زیبایی دندون", "دندانپزشکی زیبایی", "کلینیک زیبایی دندان", "طرح لبخند", "اصلاح طرح لبخند", "لبخند هالیوودی", "کاخ لبخند",
    # ۵. دندان درد (Toothache)
    "دندان درد", "دندون درد", "درد دندان", "درد دندون", "دندون خراب", "پوسیدگی دندان", "عفونت دندان", "آبسه دندان", "شکستن دندان",
    # ۶. بلیچینگ (Bleaching)
    "بلیچینگ", "بلیچینگ دندان", "بلیچینگ دندون", "سفید کردن دندان", "سفید کردن دندون", "قیمت بلیچینگ", "جرم‌گیری دندان", "بروساژ دندان",
    # ۷. روکش (Crown)
    "روکش دندان", "روکش دندون", "روکش سرامیکی", "روکش زیرکونیا", "قیمت روکش دندان", "تعویض روکش دندان", "روکش",
    # ۸. اوردنچر (Overdenture)
    "اوردنچر", "اوردنچر دندان", "اوردنچر فک", "قیمت اوردنچر", "دست دندان اوردنچر", "اوردنچر بر پایه ایمپلنت", "پروتز اوردنچر", "اوردنچر ثابت",
    # درمان ریشه و عصب‌کشی
    "عصب کشی", "عصب‌کشی دندان", "درمان ریشه دندان", "درد عصب کشی",
    # جراحی و کشیدن
    "دندان عقل", "جراحی دندان عقل", "جراحی دندان", "دندان عقل نهفته", "کشیدن دندان", "کشیدن ریشه دندان", "جراحی لثه", "پیوند استخوان فک",
    # ترمیم و پر کردن
    "پر کردن دندان", "ترمیم دندان", "پرکردگی دندان",
    # ارتودنسی
    "ارتودنسی", "ارتودنسی دندان", "ارتودنسی نامرئی", "ارتودنسی ثابت", "سیم کشی دندان",
]


def clean_child_category_name(raw_name: str) -> str:
    """Removes parentheticals and dangling prepositions to produce a clean 1-3 word Persian search query."""
    text = re.sub(r"\(.*?\)", "", str(raw_name or ""))
    text = re.sub(r"[\"\'«»!?؟,،;:؛_\-]", " ", text)
    words = [w.strip() for w in text.split() if w.strip()]
    while words and words[-1].lower() in DANGLING_CHARS_WORDS:
        words.pop()
    while words and words[0].lower() in DANGLING_CHARS_WORDS:
        words.pop(0)
    cleaned = " ".join(words[:3]) if len(words) >= 3 else " ".join(words)
    return cleaned.strip()


def build_tenant_child_queries(business, max_queries: int = MAX_INSTAGRAM_TENANT_QUERIES) -> list[str]:
    """
    Extracts concise, highly searchable product/service terms directly from the seller's
    child / subcategories without initial AI generation, capped at max_queries (default 30).
    """
    from apps.products.models import Category
    cats = Category.objects.filter(business=business).order_by("id")
    leaf_cats = [c for c in cats if not c.children.exists() or c.parent_id is not None]

    biz_name = (getattr(business, "name", "") or "").casefold()
    is_dental = "دند" in biz_name
    is_laptop = "لپ" in biz_name or "مهندسی" in biz_name or "پایتخت" in biz_name
    is_cloth = "پوشاک" in biz_name or "لباس" in biz_name

    candidates = []
    seen = set()

    def add(t):
        cleaned = clean_child_category_name(t)
        if not cleaned:
            return
        w = cleaned.split()
        if not (1 <= len(w) <= 3) or len(cleaned) < 3:
            return
        if cleaned.lower() in {"emax", "e max", "e-max", "پوشاک و مد", "پوشاک زنانه", "پوشاک مردانه", "پیراهن و پیراهن"}:
            return

        # Contextual single-word expansions
        if is_dental:
            if cleaned in {"درمان", "پاکسازی", "کشیدن", "عصب‌کشی", "جرم‌گیری", "بلیچینگ", "لمینت", "کامپوزیت", "ارتودنسی", "جراحی", "ترمیم", "روکش", "پرکردن", "اوردنچر", "زیبایی"}:
                cleaned = f"{cleaned} دندان"
            elif cleaned == "اوردنچر":
                cleaned = "اوردنچر دندان"
            elif cleaned == "زیبایی":
                cleaned = "زیبایی دندان"
            elif cleaned == "ثابت":
                cleaned = "ارتودنسی ثابت"
            elif cleaned == "دیمون":
                cleaned = "ارتودنسی دیمون"
            elif cleaned == "فوری":
                cleaned = "ایمپلنت فوری"
            elif cleaned == "سوئیسی":
                cleaned = "ایمپلنت سوئیسی"
            elif cleaned == "کره‌ای":
                cleaned = "ایمپلنت کره‌ای"
            elif cleaned == "روتاری":
                cleaned = "عصب‌کشی روتاری"
            elif cleaned in {"پر کردن", "پرکردن"}:
                cleaned = "پر کردن دندان"
            elif cleaned == "عقل":
                cleaned = "جراحی دندان عقل"
            elif cleaned == "نهفته":
                cleaned = "دندان عقل نهفته"
            elif cleaned == "زیرکونیا":
                cleaned = "روکش زیرکونیا"
            elif cleaned == "سینوس لیفت":
                cleaned = "سینوس لیفت و پیوند استخوان"
            elif cleaned == "پیوند استخوان":
                cleaned = "پیوند استخوان فک"
        elif is_laptop:
            if cleaned in {"مدیریتی", "سبک"}:
                cleaned = f"الترابوک {cleaned}"
            elif cleaned in {"برنامه‌نویسی", "مهندسی", "استوک", "دانشجویی"}:
                cleaned = f"لپ‌تاپ {cleaned}"
            elif cleaned in {"اداری 4k", "4k", "اداری", "طراحی"}:
                cleaned = f"مانیتور {cleaned}"
        elif is_cloth:
            if cleaned == "شلوار":
                cleaned = "شلوار زنانه"
            elif cleaned == "پیراهن":
                cleaned = "پیراهن مجلسی"
            elif cleaned == "راحتی":
                cleaned = "لباس راحتی"
            elif cleaned == "خواب":
                cleaned = "لباس خواب"
            elif cleaned == "زیر":
                cleaned = "لباس زیر"
            elif cleaned == "تاپ":
                cleaned = "تاپ زنانه"
            elif cleaned == "اسپرت":
                cleaned = "لباس اسپرت"

        key = cleaned.casefold()
        if key not in seen and 1 <= len(cleaned.split()) <= 3:
            seen.add(key)
            candidates.append(cleaned)

    # Prioritize general high-intent discovery terms first
    if is_dental:
        for broad in DENTAL_BROAD_DISCOVERY_QUERIES:
            add(broad)

    for c in leaf_cats:
        raw = c.name
        base = clean_child_category_name(raw)
        parts = [p.strip() for p in re.split(r"\s+و\s+|،|\s+با\s+", raw) if p.strip()]
        for p in parts:
            add(p)
        if len(base.split()) <= 3:
            add(base)

    if is_laptop:
        for extra in ["لپ‌تاپ مهندسی", "لپ‌تاپ استوک", "لپ‌تاپ برنامه‌نویسی", "مانیتور 4k", "مانیتور اداری", "الترابوک", "لپ‌تاپ دانشجویی"]:
            add(extra)
    elif is_cloth:
        for extra in ["پیراهن مردانه", "شلوار مردانه", "لباس اسپرت زنانه", "لباس اسپرت مردانه", "بلوز و شومیز", "کت و شلوار", "مانتو و پالتو", "دامن و شلوارک"]:
            add(extra)

    return candidates[:max_queries]


def ensure_family_plan(business, family, call_llm: Callable | None = None):
    """
    Builds or retrieves family plan.
    Supports both LLM planning with cache/daily cap and intelligent subcategory generation.
    """
    from .models import InstagramFamilyPlan
    from apps.billing.services import is_blocked

    if is_blocked(business):
        return None, False

    # Check cache matching exact content_hash
    cached = InstagramFamilyPlan.objects.filter(
        business=business, family_id=family.product_id, content_hash=family.content_hash(), status="ready"
    ).first()
    if cached:
        return cached, False

    # Daily plan cap: at most INSTAGRAM_PLAN_MAX_FAMILIES_PER_TENANT plans built per tenant per day.
    # Rebuilding this family's own (stale) plan does not count against the cap.
    planned_today = (
        InstagramFamilyPlan.objects.filter(business=business, built_at__date=timezone.localdate())
        .exclude(family_id=family.product_id)
        .count()
    )
    if planned_today >= INSTAGRAM_PLAN_MAX_FAMILIES_PER_TENANT:
        return None, False

    biz_name = (getattr(business, "name", "") or "").casefold()
    is_dental = "دند" in biz_name

    if call_llm is not None:
        type_names = list(business.business_types.values_list("name", flat=True)) if hasattr(business, "business_types") else []
        payload = {
            "family_path": getattr(family, "category_path", ""),
            "product_titles": (getattr(family, "titles", []) or [])[:12],
            "seller_notes": (getattr(family, "notes", "") or "")[:1000],
            "business_types": type_names,
        }
        raw_res = call_llm(PLAN_SYSTEM, json.dumps(payload, ensure_ascii=False), purpose="instagram_family_plan",
                           business_id=business.pk)
        plan_data = _validate_plan(raw_res, family=family)
    else:
        # Build queries directly from subcategories
        child_queries = build_tenant_child_queries(business, max_queries=MAX_INSTAGRAM_TENANT_QUERIES)

        fam_path = (getattr(family, "category_path", "") or "").casefold()
        fam_titles = [t.casefold() for t in getattr(family, "titles", []) or []]

        # Select queries matching this family or share tenant's core child queries
        family_kw = []
        for q in child_queries:
            if any(w in fam_path or any(w in ft for ft in fam_titles) for w in q.casefold().split()):
                family_kw.append(q)

        # For dental, ensure routine dental terms + all 8 core services are always included in every dental family
        if is_dental:
            routine_dental_tags = [
                "دندانپزشکی", "دندانپزشک", "دندان_پزشکی", "دندان_پزشک",
                "کلینیک_دندانپزشکی", "مطب_دندانپزشکی", "دندونپزشکی", "دندونپزشک",
                "دندانپزشک_خوب", "دندانپزشکی_اقساطی", "دندانپزشکی_زیبایی",
                "کامپوزیت_دندان", "لمینت_دندان", "ایمپلنت_دندان", "طرح_لبخند",
                "دندان_درد", "دندون_درد", "بلیچینگ_دندان", "روکش_دندان", "اوردنچر",
            ]
            dental_universal = [
                "دندانپزشکی", "دندونپزشکی", "دندان پزشکی", "دندون پزشکی",
                "دندانپزشک", "دندونپزشک", "کلینیک دندانپزشکی", "مطب دندانپزشکی",
                "دندانپزشک خوب", "دندانپزشک اقساطی", "دندانپزشکی اقساطی",
                "کامپوزیت دندان", "کامپوزیت",
                "لمینت دندان", "لمینت",
                "ایمپلنت دندان", "ایمپلنت",
                "زیبایی دندان", "طرح لبخند",
                "دندان درد", "دندون درد",
                "بلیچینگ دندان", "بلیچینگ",
                "روکش دندان", "روکش سرامیکی",
                "اوردنچر دندان", "اوردنچر",
                "عصب کشی",
            ]
            for u in dental_universal:
                if u not in family_kw:
                    family_kw.append(u)

        if not family_kw:
            family_kw = child_queries[:20]

        family_tags = [q.replace(" ", "_") for q in family_kw if " " in q or len(q) >= 4]
        if is_dental:
            all_tags = list(dict.fromkeys(routine_dental_tags + family_tags))[:25]
        else:
            all_tags = family_tags[:16]

        page_terms = list(dict.fromkeys(family_kw))[:15]

        plan_data = {
            "hashtags": all_tags,
            "keywords": family_kw[:35],
            "page_search_terms": page_terms,
            "buyer_signals": list(GENERIC_BUYER_SIGNALS),
            "negative_signals": ["اخبار", "سیاست", "فروشگاه عمده چینی"],
        }

    with transaction.atomic():
        row, _ = InstagramFamilyPlan.objects.update_or_create(
            business=business, family_id=family.product_id,
            defaults={
                "content_hash": family.content_hash(),
                **plan_data,
                "status": "ready",
                "error": "",
                "built_at": timezone.now()
            },
        )
    return row, True


def families_for_business(business):
    """Use the shared X family builder so Instagram and X segment products identically."""
    from django.db.models import Q
    from apps.businesses.models import Business

    b_name = (getattr(business, "name", "") or "").casefold()
    b_domain = (getattr(business, "business_domain", "") or "").casefold()
    is_dental = business.pk == 4 or "دند" in b_name or "دند" in b_domain
    if not is_dental:
        has_dental = Business.objects.filter(
            Q(id=4) | Q(name__icontains="دندان") | Q(business_domain__icontains="دندان")
        ).exists()
        if has_dental:
            return []

    from apps.billing.services import is_blocked

    if is_blocked(business):
        return []
    from apps.products.models import Product
    products = Product.objects.filter(business=business, status="ACTIVE", is_discovery_active=True,
                                      instagram_discovery_enabled=True).select_related("category")
    rows = []
    for p in products:
        rows.append(type("FamilyProduct", (), {
            "product_id": str(p.pk), "business_id": str(business.pk), "title": p.name,
            "category_path": getattr(p.category, "full_path", "") if p.category_id else "",
            "description": p.description or "", "product_type": getattr(p.category, "product_type", "") if p.category_id else "",
            "discovery_priority": getattr(p, "discovery_priority", 1), "x_search_enabled": True,
        })())
    return build_families(rows, {str(business.pk): business.search_notes or ""})


def discover_sources(business, family, plan) -> list[DiscoverySource]:
    """Plan sources plus seller-entered terms/pages, with seller entries marked as pinned."""
    out: list[DiscoverySource] = []
    seen = set()
    for source_type, values in (("hashtag", plan.hashtags), ("keyword", plan.keywords)):
        for label in values or []:
            canonical = str(label).strip().lstrip("#")
            key = (source_type, canonical.casefold())
            if canonical and key not in seen:
                seen.add(key)
                out.append(DiscoverySource(family.product_id, source_type, canonical))
    for term in plan.page_search_terms or []:
        key = ("page", f"search:{term}".casefold())
        if key not in seen:
            seen.add(key)
            out.append(DiscoverySource(family.product_id, "page", f"search:{term}"))
    from apps.products.models import Product
    product_ids = [int(i) for i in family.member_product_ids if str(i).isdigit()]
    products = Product.objects.filter(business=business, pk__in=product_ids)
    for product in products:
        for term in product.instagram_terms or []:
            key = ("keyword", str(term).casefold())
            if key not in seen:
                seen.add(key)
                out.append(DiscoverySource(family.product_id, "keyword", str(term), True))
        for tag in product.instagram_hashtags or []:
            canonical = str(tag).strip().lstrip("#")
            key = ("hashtag", canonical.casefold())
            if canonical and key not in seen:
                seen.add(key)
                out.append(DiscoverySource(family.product_id, "hashtag", canonical, True))
    for page in business.instagram_pages.filter(is_active=True):
        out.append(DiscoverySource(family.product_id, "page", page.handle.lstrip("@"), True, page))
    return out


def sample_sources(business, candidates: list[DiscoverySource], limit: int, *, rng=None) -> list[DiscoverySource]:
    """
    Selects sources for this cycle:
    1. First priority: Manual target pages due for daily check (once per 24 hours in fair round-robin order).
    2. Thompson sampling with 20% exploration for remaining capacity.
    """
    from .models import InstagramSourceStat
    from django.utils import timezone as dj_tz
    from datetime import timedelta
    rng = rng or random
    if not candidates or limit <= 0:
        return []

    daily_cutoff = dj_tz.now() - timedelta(hours=20)
    manual_pages = [s for s in candidates if s.manual and s.source_type == "page" and s.page]
    due_pages = [s for s in manual_pages if getattr(s.page, "last_run_at", None) is None or s.page.last_run_at < daily_cutoff]
    from datetime import datetime as _dt, timezone as _dt_tz
    min_date = _dt.min.replace(tzinfo=_dt_tz.utc)
    due_pages.sort(key=lambda s: getattr(s.page, "last_run_at", None) or min_date)

    pinned = []
    # Reserve slots for search queries (keywords and hashtags) so pages do not starve search queries
    max_pinned_pages = max(1, limit // 2) if limit >= 3 else limit
    if due_pages:
        pinned.extend(due_pages[:max_pinned_pages])
        if len(pinned) >= limit:
            return pinned[:limit]

    stats = {(s.family_id, s.source_type, s.label.casefold()): s for s in InstagramSourceStat.objects.filter(business=business)}
    scored = []
    for source in candidates:
        if source in pinned:
            continue
        st = stats.get((source.family_id, source.source_type, source.label.casefold()))
        alpha, beta = (st.alpha, st.beta) if st else (1.0, 1.0)
        prior = {"page": 1.5, "hashtag": 1.2, "keyword": 1.0}[source.source_type]
        bonus = 1.5 if source.manual else 1.0
        scored.append((rng.betavariate(max(.1, alpha), max(.1, beta)) * prior * bonus, source))
    scored.sort(key=lambda pair: pair[0], reverse=True)

    seen_types = {s.source_type for s in pinned}
    for _, source in scored:
        if source.manual and (source.source_type == "page" or source.source_type not in seen_types):
            pinned.append(source)
            seen_types.add(source.source_type)
            if len(pinned) >= limit:
                return pinned[:limit]
    remaining_limit = limit - len(pinned)
    remaining_candidates = [(score, source) for score, source in scored if source not in pinned]
    explore_n = max(1, round(remaining_limit * 0.2)) if len(remaining_candidates) > remaining_limit else 0
    exploit_n = max(0, remaining_limit - explore_n)
    chosen = pinned + [source for _, source in remaining_candidates[:exploit_n]]
    pool = [source for _, source in remaining_candidates if source not in chosen]
    rng.shuffle(pool)
    chosen.extend(pool[:min(explore_n, limit - len(chosen))])
    return chosen


def record_source_result(business, source: DiscoverySource, leads: int, status: str = "SUCCESS"):
    from .models import InstagramSourceStat
    key = dict(business=business, family_id=source.family_id, source_type=source.source_type, label=source.label[:100])
    row, _ = InstagramSourceStat.objects.get_or_create(**key)
    row.requests += 1
    row.leads += max(0, int(leads))
    if leads:
        row.alpha += round(min(1.0, 0.3 * float(leads)), 2)
    else:
        row.beta += 0.15 if status in {"FAILED", "THROTTLED"} else 0.5
    row.last_run_at = timezone.now()
    row.save(update_fields=["requests", "leads", "alpha", "beta", "last_run_at"])
    return row


def expand_queries_from_top_sources(business, plan, family=None, retired_label: str = "") -> list[str]:
    """
    ProQE: Progressive Query Expansion & Feedback Loop for Instagram.
    Generates high-performing general search queries inspired by confirmed buyer exemplars and winning queries.
    Strictly enforces MAX_INSTAGRAM_TENANT_QUERIES (20) per tenant.
    """
    from .models import InstagramSourceStat, InstagramEngineLead
    from apps.core.llm import chat_json
    from apps.core.prompts import get_system_prompt
    import logging
    logger = logging.getLogger(__name__)

    if not business or not plan:
        return []

    # 1. Check total active queries count for this tenant
    current_stats = list(InstagramSourceStat.objects.filter(business=business).order_by("-alpha", "-leads"))
    total_tenant_queries = len(current_stats)

    # 2. Fetch real confirmed buyer exemplars from InstagramEngineLead
    exemplars = []
    try:
        leads_qs = InstagramEngineLead.objects.filter(
            business=business,
            buyer_score__gte=0.5,
        ).order_by("-id")[:5]
        for l in leads_qs:
            text = (l.comment_text or l.lead_text or "").strip()
            if text:
                exemplars.append(text[:120])
    except Exception:
        pass

    # 3. Top winning queries (alpha >= 1.5)
    top_stats = [s for s in current_stats if s.alpha >= 1.5 and s.label != retired_label][:6]
    successful_labels = [s.label for s in top_stats]
    if not successful_labels:
        successful_labels = (list(plan.keywords or []) + list(plan.hashtags or []))[:4]

    # Child categories context
    child_queries = build_tenant_child_queries(business, max_queries=MAX_INSTAGRAM_TENANT_QUERIES)

    prompt_system = """You are the ProQE (Progressive Query Expansion) engine for Persian buyer discovery on Instagram.
You see confirmed buyer comments (real exemplars) and winning queries. Your task: propose 2-3 NEW general, high-intent Persian search terms.

HARD RULES:
1. Every phrase must be 1, 2 or 3 Persian words. Short and highly natural.
2. Must be GENERAL consumer search words matching the product categories (e.g. «پیراهن مردانه», «ایمپلنت دندان», «لپ‌تاپ استوک», «مانتو تابستانه»).
3. NEVER generate awkward, hyper-niche, or seller-irrelevant words like «اپ استور», «تولیدی شومیز», or single useless adjectives.
4. Return strictly JSON:
{
  "new_keywords": ["short phrase 1", "short phrase 2"],
  "new_hashtags": ["tag1", "tag2"]
}
"""
    user_payload = {
        "business_name": business.name,
        "taxonomy_categories": child_queries[:12],
        "winning_queries": successful_labels,
        "retired_query": retired_label or "",
        "confirmed_buyer_exemplars": exemplars[:4],
    }

    try:
        res = chat_json(
            get_system_prompt("proqe_expand_ig", prompt_system),
            json.dumps(user_payload, ensure_ascii=False),
            purpose="proqe_expand_instagram",
            business_id=business.pk
        )
    except Exception as exc:
        logger.warning(f"Failed ProQE Instagram query expansion: {exc}")
        return []

    if not isinstance(res, dict):
        return []

    new_tags = _phrases(res.get("new_hashtags"), limit=2, words_max=3, max_len=40)
    new_kw = _phrases(res.get("new_keywords"), limit=2, words_max=3, max_len=60)

    mega = {"لباس", "فروشگاه", "تخفیف", "پوشاک", "shop", "store", "fashion"}
    added = []

    current_tags = list(plan.hashtags or [])
    current_kw = list(plan.keywords or [])

    # If already at or above cap (20), prune the weakest queries (lowest score) to make room
    for kw in new_kw:
        clean_kw = clean_child_category_name(kw)
        if not clean_kw or len(clean_kw.split()) > 3 or clean_kw.casefold() in [k.casefold() for k in current_kw]:
            continue
        if total_tenant_queries >= MAX_INSTAGRAM_TENANT_QUERIES:
            weakest = InstagramSourceStat.objects.filter(business=business).order_by("alpha", "leads").first()
            if weakest:
                weakest.delete()
                total_tenant_queries -= 1

        current_kw.append(clean_kw)
        added.append(clean_kw)
        InstagramSourceStat.objects.get_or_create(
            business=business,
            family_id=plan.family_id,
            source_type="keyword",
            label=clean_kw[:100],
            defaults={"alpha": 1.0, "beta": 1.0}
        )
        total_tenant_queries += 1

    for tag in new_tags:
        clean_tag = tag.replace(" ", "_").strip()
        if clean_tag.casefold() in mega or clean_tag.casefold() in [t.casefold() for t in current_tags]:
            continue
        if total_tenant_queries >= MAX_INSTAGRAM_TENANT_QUERIES:
            break
        current_tags.append(clean_tag)
        added.append(f"#{clean_tag}")
        InstagramSourceStat.objects.get_or_create(
            business=business,
            family_id=plan.family_id,
            source_type="hashtag",
            label=clean_tag[:100],
            defaults={"alpha": 1.0, "beta": 1.0}
        )
        total_tenant_queries += 1

    if added:
        plan.keywords = current_kw[:MAX_INSTAGRAM_TENANT_QUERIES]
        plan.hashtags = current_tags[:MAX_INSTAGRAM_TENANT_QUERIES]
        plan.save(update_fields=["keywords", "hashtags"])
        logger.info(f"ProQE expanded {len(added)} queries for family {plan.family_id}: {added}")

    return added


def cheap_buyer_match(text: str, buyer_signals: list[str], negative_signals: list[str] | None = None) -> bool:
    normalized = re.sub(r"\s+", " ", (text or "").casefold()).strip()
    if not normalized:
        return False
    if any(str(signal).casefold() in normalized for signal in negative_signals or []):
        return False
    return any(signal.casefold() in normalized for signal in [*GENERIC_BUYER_SIGNALS, *(buyer_signals or [])])


def lead_quality_score(base: float, *, is_business: bool | None, follower_count: int | None,
                       bio: str, seller_notes: str, family_path: str, product_titles: list[str]) -> float:
    """Small, explainable boost from public profile fields already returned with comments; never fetches extra data."""
    score = max(0.0, min(1.0, float(base or 0)))
    b2b = any(term in (seller_notes or "").casefold() for term in ("عمده", "فروشگاه", "تولیدی", "همکاری", "b2b"))
    if is_business is True:
        score += 0.06 if b2b else -0.02
    elif is_business is False and not b2b:
        score += 0.03
    bio_norm = (bio or "").casefold()
    terms = [*str(family_path or "").split(">"), *(product_titles or [])]
    terms = [t.strip().casefold() for t in terms if len(t.strip()) >= 3]
    if any(term in bio_norm for term in terms):
        score += 0.03
    if follower_count is not None and 1000 <= int(follower_count) < 100000:
        score += 0.01
    return max(0.0, min(1.0, score))
