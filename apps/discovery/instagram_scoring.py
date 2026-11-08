"""
Instagram Lead Priority Scoring Engine.

Implements the official 45-point scoring and outreach priority algorithm:
1. Freshness Window: 30-day cutoff with gradual score decay.
   Age reduces the freshness score gradually but does not disqualify the lead.
2. Profile Privacy (15 points):
   - Public account: +15 points
   - Private account: 0 points
3. Comment Type and Buyer Intent (15 points):
   - Need-based Consultative Question (QUESTION_INQUIRY): +15 points
     (inquiries regarding installments, procedure steps, duration, feasibility, pain/complications)
   - Direct Short Prompt / Call-to-Action (DIRECT_SHORT_CTA): +15 points
     (single-word, number, 1-2 word comments or explicit CTA responses like "نیک", "قیمت", "۱", "۲", "دایرکت", "من")
4. Target Competitor / Watched Pages Bonus (4 points):
   - Extracted from configured clinic target pages: +4 points
5. Dental Services Priority Bonus (1 to 6 points):
   - 1. کامپوزیت (Composite): +6 points
   - 2. لمینت (Laminate): +5 points
   - 3. ایمپلنت / کاشت (Implant): +4 points
   - 4. بلیچینگ / سفید کردن (Bleaching): +3 points
   - 5. روکش (Crown): +2 points
   - 6. اوردنچر (Overdenture): +1 point
6. Freshness Bonus (0 to 5 points):
   - Today (age < 24h): +5 points
   - 1 day ago (24h <= age < 48h): +4 points
   - 2 days ago (48h <= age < 72h): +3 points
   - 3 days ago (72h <= age < 96h): +2 points
   - 4-5 days ago (96h <= age <= 120h): +1 point
   - > 5 days: 0 points (and disqualified)

Total Score Range: 0 to 45 points.
Highest Priority: 45 points (Public[15] + CTA/Question[15] + Today[5] + TargetPage[4] + Composite[6]).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from django.utils import timezone as django_tz

logger = logging.getLogger("discovery.instagram_scoring")

# Question & Consultative Need Indicators
QUESTION_MARK_PATTERN = re.compile(r"[؟?]")

INQUIRY_KEYWORDS = [
    # Installment & Financial Terms
    "اقساط", "قسطی", "قسط", "شرایط اقساط", "اقساطی", "چک", "چک صیادی",
    "تخفیف", "بیمه", "تعرفه", "هزینش", "قیمتش", "چند تومان", "چقدر میشه",
    # Procedure, Duration & Steps
    "چطوری", "چطور", "چگونه", "به چه نحوه", "نحوه", "چند جلسه", "چند وقت",
    "مراحل", "مدت زمان", "طول میکشه", "روال", "چند روز",
    # Fear, Pain & Complications
    "درد داره", "عوارض", "عوارض داره", "خراب نمیشه", "تراش میخوره",
    "تراش", "آسیب میزنه", "بی حسی", "بیهوشی",
    # Feasibility & Consultation
    "امکانش هست", "میشه انجام داد", "برای دندون من", "ویزیت", "مشاوره",
    "عکس بفرستم", "معاینه", "نوبت", "رزرو", "وقت بدید", "آیا",
]

DIRECT_CTA_KEYWORDS = [
    "قیمت", "هزینه", "دایرکت", "کامنت", "ارسال", "بفرستید",
    "ارسال کنید", "سفارش", "اطلاعات", "شرایط", "من", "چنده",
    "موجوده", "لطفا بفرستید", "لطفا دایرکت", "لطفاً بفرستید",
    "قیمت لطفا", "هزینه لطفا", "لینک", "شماره تماس", "پیام بدید",
    "نیک", "سرو", "تخفیف", "سلام", "پیج", "ادرس", "آدرس",
]

# 45 Target Dental Pages for Sarv Dental Clinic
TARGET_DENTAL_PAGES = {
    "sarv_dental_plus", "sarv_dental", "sarvbeauteeth.clinic", "sarv_dental_vip",
    "sarv_luxury", "sarv_smile_vip", "ir_dandan", "imamrezaclinic.ir",
    "tabassomdental.clinic", "farbod_dental", "niazdent1", "melinadental",
    "artman.dental", "_omidan_salamat", "dental.allmas", "dr.marziehhoseini",
    "exir_dentalclinic", "raya_dental_clinic", "dental_land_", "drmohammadzadehkids",
    "setare_shargh_dental", "dr.ghazaleh.keyvani", "robin.dental", "mahdentclinic",
    "dr.lamisarkawazi", "dentaj_clinic", "clinicdey.dental", "drhamedmahmoudi.omfs",
    "dr.farbodrayatnia", "dr.abbasbabazadeh", "karen_dentalclinic", "roshadent_clinic",
    "dr.roshaclinic", "roshaclinic.ir", "roshadent", "mehrclinic_dental",
    "dentis_navid_salamat", "behesht.dnt", "mahan__dental1", "mahrad_dental_clinic",
    "rad_dental_clinic1", "nikan.dental.clinic.beh", "bahara.dental.clinic",
    "composite_dentistry", "dental_almas",
}

# 6 Priority Dental Services (Rank 1 to 6)
DENTAL_SERVICES_ORDER = [
    ("COMPOSITE", 6, "کامپوزیت", [
        "کامپوزیت", "composite", "ونیر کامپوزیت", "کامپوزیت ونیر", "کامپوزیت دندان",
        "کامپوزیت لیرینگ", "سارمکو", "کاریزما", "توکویاما", "ips", "باندینگ", "ترمیم زیبایی"
    ]),
    ("LAMINATE", 5, "لمینت", [
        "لمینت", "laminate", "لمینیت", "ونیر لمینت", "لمینت سرامیکی", "سرامیکی",
        "ایمکس", "e-max", "emax", "پرسلن", "طرح لبخند سرامیکی"
    ]),
    ("IMPLANT", 4, "ایمپلنت", [
        "ایمپلنت", "implant", "کاشت دندان", "کاشت", "پیوند استخوان", "سینوس لیفت",
        "ایمپلنت فوری", "ایمپلنت دیجیتال", "فیکسچر", "ایمپلنت سوئیسی", "ایمپلنت کره‌ای"
    ]),
    ("BLEACHING", 3, "بلیچینگ", [
        "بلیچینگ", "bleaching", "سفید کردن", "سفیدکردن", "سفید کردن دندان",
        "جرم‌گیری", "جرم گیری", "بروساژ", "بلیچینگ هوم", "بلیچینگ آفیس", "سفیدسازی"
    ]),
    ("CROWN", 2, "روکش", [
        "روکش", "crown", "روکش زیرکونیا", "زیرکونیا", "روکش سرامیکی", "روکش دندان",
        "پست و روکش", "بریج", "روکش فلزی", "پروتز ثابت"
    ]),
    ("OVERDENTURE", 1, "اوردنچر", [
        "اوردنچر", "overdenture", "دست دندان", "پروتز متحرک", "دندان مصنوعی",
        "پروتز کامل", "اوردنچر بر پایه ایمپلنت"
    ]),
]


def classify_comment_intent(comment_text: str, cta_word: str = "") -> Tuple[str, int, str]:
    """
    Classifies comment intent:
    Returns (intent_type, points, persian_reason)
    """
    if not comment_text or not comment_text.strip():
        return "OTHER", 0, "کامنت خالی یا نامعتبر"

    clean_text = comment_text.strip()
    norm_text = clean_text.casefold()

    # Reject pure emojis
    is_pure_emoji = bool(re.fullmatch(r"[\s\U00010000-\U0010ffff\u2600-\u27bf\u2300-\u23ff\u2b50]+", clean_text))
    if is_pure_emoji:
        return "OTHER", 0, "کامنت صرفاً حاوی شکلک و ایموجی (فاقد متن)"

    words = clean_text.split()
    word_count = len(words)

    # 1. Check for Need-based Inquiry / Question (QUESTION_INQUIRY -> 15 points)
    has_question_mark = bool(QUESTION_MARK_PATTERN.search(clean_text))
    matched_inquiry_kw = next((kw for kw in INQUIRY_KEYWORDS if kw in norm_text), None)

    if has_question_mark or matched_inquiry_kw:
        reason = f"سوال مشاوره‌ای و استعلام نیاز ({matched_inquiry_kw or 'علامت سوال'})"
        return "QUESTION_INQUIRY", 15, reason

    # 2. Check for Single-Word, Numbers, or 1-2 Word Mass Responses (DIRECT_SHORT_CTA -> 15 points)
    # Users responding to call-to-action frequently leave 1-2 words or digits
    is_digit_or_number = clean_text.isdigit() or any(char.isdigit() for char in clean_text) and word_count <= 2
    matched_cta_kw = next((kw for kw in DIRECT_CTA_KEYWORDS if kw in norm_text), None)
    matches_post_cta = bool(cta_word and len(cta_word) >= 2 and cta_word.casefold() in norm_text)

    if word_count <= 2 or is_digit_or_number or matched_cta_kw or matches_post_cta:
        trigger_desc = matched_cta_kw or cta_word or (clean_text if word_count <= 2 else "پاسخ کوتاه")
        return "DIRECT_SHORT_CTA", 15, f"پاسخ کوتاه کال‌تواکشن و استعلام ({trigger_desc})"

    # 3. Check for 3 to 4 word short prompts
    if word_count <= 4 and (matched_cta_kw or matches_post_cta):
        return "DIRECT_SHORT_CTA", 15, f"پیام کوتاه استعلام و کال‌تواکشن ({matched_cta_kw or cta_word})"

    # 4. Check for general buyer intent keywords in longer comments
    for kw in ["دندان", "کامپوزیت", "لمینت", "ایمپلنت", "ارتودنسی", "عصب کشی", "ترمیم", "روکش", "دندانپزشک"]:
        if kw in norm_text:
            return "GENERAL_BUYER", 10, f"اشاره مستقیم به خدمت دندانپزشکی ({kw})"

    return "OTHER", 0, "کامنت عمومی یا سایر"


def detect_dental_service_priority(text: str, caption: str = "", cta_word: str = "") -> Tuple[str, int, str]:
    """
    Detects dental service and assigns priority points from 6 to 1:
    1. کامپوزیت (+6)
    2. لمینت (+5)
    3. ایمپلنت (+4)
    4. بلیچینگ (+3)
    5. روکش (+2)
    6. اوردنچر (+1)
    """
    combined = f"{text or ''} {caption or ''} {cta_word or ''}".casefold()
    for service_code, pts, fa_name, keywords in DENTAL_SERVICES_ORDER:
        for kw in keywords:
            if kw.casefold() in combined:
                return service_code, pts, fa_name

    return "GENERAL", 0, "عمومی دندانپزشکی"


def detect_target_page_bonus(source_page_username: str, source_post_url: str = "") -> Tuple[int, str]:
    """
    Checks if the source page is in the 45 dedicated clinic target accounts.
    Returns (+4, reason) if matched, otherwise (0, "").
    """
    clean_user = (source_page_username or "").strip().lstrip("@").lower()
    clean_url = (source_post_url or "").lower()

    if clean_user and clean_user in TARGET_DENTAL_PAGES:
        return 4, f"پیج هدف اختصاصی کلینیک (@{clean_user})"

    if clean_user and clean_user.startswith("sarv_"):
        return 4, f"پیج هدف مجموعه سرو (@{clean_user})"

    for target in TARGET_DENTAL_PAGES:
        if target in clean_url:
            return 4, f"پیج هدف اختصاصی کلینیک ({target})"

    return 0, ""


DEFAULT_OUTREACH_CUTOFF_DAYS = 30


def get_outreach_cutoff_days() -> int:
    try:
        from django.conf import settings
        return int(getattr(settings, "INSTAGRAM_OUTREACH_CUTOFF_DAYS", DEFAULT_OUTREACH_CUTOFF_DAYS))
    except Exception:
        return DEFAULT_OUTREACH_CUTOFF_DAYS


def calculate_freshness_score(
    comment_time: Optional[datetime],
    now: Optional[datetime] = None,
    max_age_days: Optional[int] = None,
) -> Tuple[int, float, bool]:
    """
    Calculates freshness bonus (0 to 5 points) with gradual decay across the cutoff window.
    Age reduces the score gradually, but does not mark a lead ineligible.
    Returns:
        (freshness_score: int, age_days: float, is_eligible: bool)
    """
    if now is None:
        now = django_tz.now()

    if comment_time is None:
        return 2, 2.5, True

    if django_tz.is_naive(comment_time):
        comment_time = django_tz.make_aware(comment_time, timezone.utc)

    delta = now - comment_time
    age_seconds = max(0.0, delta.total_seconds())
    age_days = round(age_seconds / 86400.0, 2)

    cutoff_days = max_age_days if max_age_days is not None else get_outreach_cutoff_days()

    if age_days < 1.0:
        freshness_score = 5
    elif age_days < 2.0:
        freshness_score = 4
    elif age_days < 4.0:
        freshness_score = 3
    elif age_days < 7.0:
        freshness_score = 2
    elif age_days <= float(cutoff_days):
        freshness_score = 1
    else:
        freshness_score = 0

    return freshness_score, age_days, True


def calculate_success_probability(
    outreach_score: int,
    is_private: bool,
    intent_type: str,
    age_days: float,
    service_score: int = 0,
    target_page_score: int = 0,
    comment_text: str = "",
) -> int:
    """
    Calculates conversion/success probability percentage (25% to 98%)
    based on the 45-point scoring system.
    """
    base_pct = int(round((outreach_score / 45.0) * 84.0))
    bonus = 0
    if not is_private:
        bonus += 4
    if intent_type in ("QUESTION_INQUIRY", "DIRECT_SHORT_CTA"):
        bonus += 4
    if service_score >= 4:
        bonus += 3
    elif service_score > 0:
        bonus += 2
    if target_page_score > 0:
        bonus += 2
    if age_days <= 1.0:
        bonus += 5
    elif age_days <= 2.0:
        bonus += 3
    elif age_days <= 3.0:
        bonus += 1

    return min(98, max(25, base_pct + bonus))


def calculate_lead_outreach_score(
    comment_text: str,
    comment_time: Optional[datetime],
    is_private: bool,
    cta_word: str = "",
    source_page_username: str = "",
    post_caption: str = "",
    source_post_url: str = "",
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Calculates full 45-point lead outreach score and success probability.
    Returns breakdown dict with all components.
    """
    if now is None:
        now = django_tz.now()

    # 1. Profile privacy score (15 points for public, 0 for private)
    public_score = 0 if is_private else 15
    profile_label = "خصوصی (Private)" if is_private else "عمومی (Public)"

    # 2. Intent score (15 points for Question or Direct CTA)
    intent_type, intent_score, intent_reason = classify_comment_intent(comment_text, cta_word=cta_word)

    # 3. Freshness score (0 to 5 points, max age 5 days)
    freshness_score, age_days, is_eligible = calculate_freshness_score(comment_time, now=now)

    # 4. Target Dental Page Bonus (+4 points)
    target_page_score, target_page_reason = detect_target_page_bonus(
        source_page_username=source_page_username,
        source_post_url=source_post_url,
    )

    # 5. Dental Service Priority Bonus (+1 to +6 points)
    service_code, service_score, service_name = detect_dental_service_priority(
        text=comment_text,
        caption=post_caption,
        cta_word=cta_word,
    )

    # Total Score (Max 45: 15 Public + 15 Intent + 5 Freshness + 4 Target Page + 6 Service)
    total_score = public_score + intent_score + freshness_score + target_page_score + service_score

    # Human-readable age description in Persian
    cutoff_days = get_outreach_cutoff_days()
    if age_days < 0.08:
        age_label = "چند دقیقه پیش"
    elif age_days < 1.0:
        age_label = "امروز (زیر ۲۴ ساعت)"
    elif age_days < 2.0:
        age_label = "دیروز (۱ روز پیش)"
    elif age_days < 3.0:
        age_label = "۲ روز پیش"
    elif age_days < 4.0:
        age_label = "۳ روز پیش"
    elif age_days <= 7.0:
        age_label = f"{int(age_days)} روز پیش"
    elif age_days <= float(cutoff_days):
        age_label = f"{int(age_days)} روز پیش"
    else:
        age_label = f"بیش از {cutoff_days} روز ({int(age_days)} روز پیش)"

    # Calculate Success / Conversion Probability %
    success_probability = calculate_success_probability(
        outreach_score=total_score,
        is_private=is_private,
        intent_type=intent_type,
        age_days=age_days,
        service_score=service_score,
        target_page_score=target_page_score,
        comment_text=comment_text,
    )

    return {
        "total_score": total_score,
        "success_probability": success_probability,
        "is_eligible": is_eligible,
        "public_score": public_score,
        "is_private": is_private,
        "profile_label": profile_label,
        "intent_type": intent_type,
        "intent_score": intent_score,
        "intent_reason": intent_reason,
        "freshness_score": freshness_score,
        "age_days": age_days,
        "age_label": age_label,
        "target_page_score": target_page_score,
        "target_page_reason": target_page_reason,
        "service_code": service_code,
        "service_score": service_score,
        "service_name": service_name,
    }


def update_lead_scoring(lead: Any, commit: bool = True, now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Evaluates and updates score fields on an InstagramEngineLead instance.
    """
    c_time = getattr(lead, "comment_created_at", None) or getattr(lead, "created_at", None)
    is_priv = bool(getattr(lead, "is_private", False))
    c_text = getattr(lead, "comment_text", "") or ""
    cta = getattr(lead, "matched_cta", "") or ""
    source_user = getattr(lead, "source_page_username", "") or ""
    post_caption = getattr(lead, "post_caption_snippet", "") or ""
    post_url = getattr(lead, "source_post_url", "") or ""

    calc = calculate_lead_outreach_score(
        comment_text=c_text,
        comment_time=c_time,
        is_private=is_priv,
        cta_word=cta,
        source_page_username=source_user,
        post_caption=post_caption,
        source_post_url=post_url,
        now=now,
    )

    lead.outreach_score = calc["total_score"]
    lead.success_probability = calc.get("success_probability", 0)
    lead.comment_intent_type = calc["intent_type"]
    lead.score_breakdown = calc

    if not lead.comment_created_at and c_time:
        lead.comment_created_at = c_time

    if commit:
        update_fields = [
            "outreach_score", "success_probability", "comment_intent_type",
            "score_breakdown", "updated_at"
        ]
        if not getattr(lead, "_state", None) or getattr(lead, "_state").adding is False:
            lead.save(update_fields=update_fields)

    return calc


def backfill_all_leads_scores(tenant_id: Optional[str] = None) -> Tuple[int, int]:
    """
    Backfills outreach_score, success_probability, and crawled post metadata for existing leads.
    """
    import re
    from apps.discovery.models import InstagramEngineLead, InstagramCrawledPost

    qs = InstagramEngineLead.objects.all()
    if tenant_id:
        qs = qs.filter(tenant_id=tenant_id)

    # Pre-build lookup map of media_code -> (account_username, posted_at, caption)
    crawled_map = {}
    for p in InstagramCrawledPost.objects.only("media_code", "account_username", "posted_at", "caption").iterator(chunk_size=500):
        if p.media_code:
            crawled_map[p.media_code] = (p.account_username, p.posted_at, p.caption)

    total = 0
    high_score = 0
    now = django_tz.now()

    for lead in qs.iterator(chunk_size=200):
        # Link source post metadata if missing
        m = re.search(r"/p/([A-Za-z0-9_-]+)", lead.source_post_url or "")
        if m:
            code = m.group(1)
            meta = crawled_map.get(code)
            if meta:
                acc_user, p_date, p_caption = meta
                if not lead.source_page_username and acc_user:
                    lead.source_page_username = acc_user
                if not lead.post_created_at and p_date:
                    lead.post_created_at = p_date
                if not lead.post_caption_snippet and p_caption:
                    lead.post_caption_snippet = p_caption[:1000]

        res = update_lead_scoring(lead, commit=False, now=now)

        lead.save(update_fields=[
            "outreach_score", "success_probability", "comment_intent_type",
            "score_breakdown", "source_page_username", "post_created_at",
            "post_caption_snippet", "updated_at"
        ])
        total += 1
        if res["total_score"] >= 30:
            high_score += 1

    logger.info("Backfilled %d leads. High score (>= 30): %d", total, high_score)
    return total, high_score
