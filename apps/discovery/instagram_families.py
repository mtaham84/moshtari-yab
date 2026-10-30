"""Cached per-family Instagram plans, source sampling and the cheap comment funnel."""
from __future__ import annotations

import json
import os
import random
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from workers.x_collector.families import build_families

INSTAGRAM_PLAN_MAX_FAMILIES_PER_TENANT = max(1, int(os.getenv("INSTAGRAM_PLAN_MAX_FAMILIES_PER_TENANT", "8")))
INSTAGRAM_PAGE_MIN_COMMENTS = max(1, int(os.getenv("INSTAGRAM_PAGE_MIN_COMMENTS", "20")))
INSTAGRAM_MAX_SOURCES_PER_TENANT_CYCLE = max(1, int(os.getenv("INSTAGRAM_MAX_SOURCES_PER_TENANT_CYCLE", "4")))
INSTAGRAM_DAILY_REQUEST_BUDGET = max(1, int(os.getenv("INSTAGRAM_DAILY_REQUEST_BUDGET", "150")))
PLAN_SYSTEM = """You are a senior Instagram discovery strategist for Persian-speaking (Iran) sellers. A FAMILY is one seller + one category
node (e.g. «پوشاک > مردانه > پیراهن») with the titles of his products and his own NOTES about the customers he wants and how
he sells. Plan where REAL BUYERS of this family can be found on Instagram. Buyers rarely use hashtags: they write
«قیمت؟», «موجوده؟», «پیام بدید», «سایز؟» in the comments under posts of sellers and niche accounts. So plan three source types.

1) hashtags (6-10): Persian or finglish tags a buyer-oriented post about this family would carry, WITHOUT the # sign.
   Product-type level (1-3 words joined by _): «لباس_مجلسی_مردانه», «جوراب_ورزشی», «ایمپلنت_دندان».
   Mid-sized niche tags only: never mega tags («لباس», «فروشگاه», «تخفیف», «پوشاک») and never ultra-narrow ones
   (colours, sizes, model numbers, brands, city names unless the notes demand a city). No spam tags (follow4follow...).
2) keywords (4-8): 1-3 word Persian phrases for Instagram text search that find posts by sellers/educators of the same
   family or posts where people ask for it («دامن چرم», «کلاس کنکور», «خرید عمده جوراب»).
3) page_search_terms (4-8): 1-3 word phrases to FIND relevant business accounts (peer sellers, wholesalers, schools,
   clinics) through Instagram account search. NEVER output Instagram handles or URLs: you do not know real ones and
   they will be verified by code.
Also return buyer_signals (6-12): short Persian phrases that appear in buyer comments for this family
(«قیمت», «موجود», «سایز», «عمده», «دایرکت»). And negative_signals (0-8): words that mark spam/sellers/irrelevance.
SELLER NOTES ARE BINDING: wholesale -> emphasise buyer shops and B2B words («عمده», «فروشگاه», «همکاری»); students/clients ->
consumer words; a city/price segment -> reflect it. Stay inside THIS family; do not drift to neighbouring categories.
Return strict JSON:
{"hashtags":[...],"keywords":[...],"page_search_terms":[...],"buyer_signals":[...],"negative_signals":[...]}"""

_HANDLE_OR_URL = re.compile(r"(?:^\s*@|instagram\.com|https?://|www\.)", re.I)
_BAD_TERM_CHARS = re.compile(r"[\n\r,،;؛!?؟@#]" )
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


def _validate_plan(data: Any) -> dict[str, list[str]]:
    if not isinstance(data, dict):
        raise ValueError("پاسخ طرح اینستاگرام معتبر نیست")
    fields = {
        "hashtags": (10, 3, 40), "keywords": (8, 3, 60), "page_search_terms": (8, 3, 60),
        "buyer_signals": (12, 4, 50), "negative_signals": (8, 3, 50),
    }
    plan = {key: _phrases(data.get(key), limit=limit, words_max=words, max_len=max_len)
            for key, (limit, words, max_len) in fields.items()}
    mega = {"لباس", "فروشگاه", "تخفیف", "پوشاک", "shop", "store", "fashion"}
    plan["hashtags"] = [tag for tag in plan["hashtags"] if " " not in tag and tag.casefold() not in mega]
    if (len(plan["hashtags"]) < 6 or len(plan["keywords"]) < 4 or len(plan["page_search_terms"]) < 4
            or len(plan["buyer_signals"]) < 6):
        raise ValueError("پاسخ طرح اینستاگرام ناقص است")
    return plan


def ensure_family_plan(business, family, call_llm: Callable | None = None):
    """Return the cache if its family hash matches; otherwise build at most eight new plans per tenant/day."""
    from .models import InstagramFamilyPlan

    cached = InstagramFamilyPlan.objects.filter(business=business, family_id=family.product_id,
                                                   content_hash=family.content_hash(), status="ready").first()
    if cached:
        return cached, False
    today = timezone.localdate()
    planned_today = InstagramFamilyPlan.objects.filter(business=business, built_at__date=today).count()
    if planned_today >= INSTAGRAM_PLAN_MAX_FAMILIES_PER_TENANT:
        return None, False
    if call_llm is None:
        from apps.core.llm import chat_json
        call_llm = chat_json
    type_names = list(business.business_types.values_list("name", flat=True))
    payload = {
        "family_path": family.category_path,
        "product_titles": family.titles[:12],
        "seller_notes": (family.notes or "")[:1000],
        "business_types": type_names,
    }
    result = call_llm(PLAN_SYSTEM, json.dumps(payload, ensure_ascii=False), purpose="instagram_family_plan",
                      business_id=business.pk)
    plan = _validate_plan(result)
    with transaction.atomic():
        row, _ = InstagramFamilyPlan.objects.update_or_create(
            business=business, family_id=family.product_id,
            defaults={"content_hash": family.content_hash(), **plan, "status": "ready", "error": "", "built_at": timezone.now()},
        )
    return row, True


def families_for_business(business):
    """Use the shared X family builder so Instagram and X segment products identically."""
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
    """Thompson sample with 20% exploration; page priors and manual sources get a bonus."""
    from .models import InstagramSourceStat
    rng = rng or random
    if not candidates or limit <= 0:
        return []
    stats = {(s.family_id, s.source_type, s.label.casefold()): s for s in InstagramSourceStat.objects.filter(business=business)}
    scored = []
    for source in candidates:
        st = stats.get((source.family_id, source.source_type, source.label.casefold()))
        alpha, beta = (st.alpha, st.beta) if st else (1.0, 1.0)
        prior = {"page": 1.5, "hashtag": 1.2, "keyword": 1.0}[source.source_type]
        bonus = 1.5 if source.manual else 1.0
        scored.append((rng.betavariate(max(.1, alpha), max(.1, beta)) * prior * bonus, source))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    pinned, seen_types = [], set()
    for _, source in scored:
        if source.manual and source.source_type not in seen_types:
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
        row.alpha += int(leads)
    else:
        row.beta += 0.25 if status in {"FAILED", "THROTTLED"} else 0.5
    row.last_run_at = timezone.now()
    row.save(update_fields=["requests", "leads", "alpha", "beta", "last_run_at"])
    return row


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
