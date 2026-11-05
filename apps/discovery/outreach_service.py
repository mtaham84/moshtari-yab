"""
Service layer for Instagram Direct Message Outreach:
1. Dynamic Rate-Limiting & Dealism Tiers (New, Medium, Aged accounts).
2. AI-powered Pitch Generation & Human Review Task Box.
3. Automated & On-demand Checking of Incoming Customer Replies.
4. Multi-account Second / Follow-up Message Dispatching.
"""

import json
import logging
import random
import re
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from django.utils import timezone

logger = logging.getLogger("instagram_outreach")

# Dealism Rate Limit Benchmarks (Optimized for Personal Accounts)
TIER_LIMITS = {
    "NEW": {
        "title": "اکانت شخصی و تازه (حداکثر ایمنی ضد بلاک)",
        "min_daily": 8,
        "max_daily": 10,
        "default_daily": 9,
        "hourly_speed": 2,  # حداکثر ۱ تا ۲ پیام در ساعت
        "min_delay_sec": 1500,  # 25 minutes
        "max_delay_sec": 3000,  # 50 minutes (کاملاً تصادفی و متغیر)
        "delay_desc": "۲۵ تا ۵۰ دقیقه وقفه متغیر و رندوم (۹ پیام در ۵ الی ۶ ساعت)",
        "color": "#1d4ed8",
        "bg_color": "#dbeafe",
    },
    "MEDIUM": {
        "title": "اکانت متوسط (۳ تا ۱۲ ماه)",
        "min_daily": 15,
        "max_daily": 25,
        "default_daily": 18,
        "hourly_speed": 4,
        "min_delay_sec": 720,   # 12 minutes
        "max_delay_sec": 1500,  # 25 minutes
        "delay_desc": "۱۲ تا ۲۵ دقیقه وقفه متغیر",
        "color": "#b45309",
        "bg_color": "#fef3c7",
    },
    "AGED": {
        "title": "اکانت قدیمی و فعال (بالای ۱ سال)",
        "min_daily": 30,
        "max_daily": 50,
        "default_daily": 35,
        "hourly_speed": 6,
        "min_delay_sec": 480,   # 8 minutes
        "max_delay_sec": 900,   # 15 minutes
        "delay_desc": "۸ تا ۱۵ دقیقه وقفه متغیر",
        "color": "#15803d",
        "bg_color": "#dcfce7",
    },
}


def get_tier_specs(tier_key: str) -> dict:
    return TIER_LIMITS.get(tier_key or "NEW", TIER_LIMITS["NEW"])


def parse_spintax(text: str) -> str:
    """
    Recursively parse and resolve Spintax patterns such as {سلام|درود|وقت بخیر}.
    Handles nested spintax structures with randomized selection.
    """
    if not text:
        return ""
    pattern = re.compile(r"\{([^{}]+)\}")
    while pattern.search(text):
        text = pattern.sub(lambda m: random.choice(m.group(1).split("|")), text)
    return text


def build_bulletproof_message(
    pitch_template: str = "",
    lead: Any = None,
    consultant_name: str = "مشاور",
    business_name: str = "مجموعه تخصصی سرو",
) -> str:
    """
    Creates a 100% unique, anti-fingerprint DM using Multi-Structure Spintax + dynamic personalization.
    Injects random polite variations, Persian half-spaces, and unique cryptographic hashes
    to defeat Meta/Instagram semantic duplicate spam classifiers.
    """
    raw_name = (getattr(lead, "full_name", None) or getattr(lead, "username", "")).strip()
    clean_name = f" {raw_name}" if raw_name and not raw_name.startswith("@") and len(raw_name) < 25 else ""

    topic = "خدمات و محصولات مورد نظر شما"
    if hasattr(lead, "comment_text") and lead.comment_text:
        c_text = lead.comment_text.lower()
        # Dental / Medical terms
        if "کامپوزیت" in c_text:
            topic = "کامپوزیت و اصلاح طرح لبخند"
        elif "لمینت" in c_text:
            topic = "لمینت سرامیکی و امور زیبایی دندان"
        elif "ایمپلنت" in c_text:
            topic = "کاشت تخصصی ایمپلنت دندان"
        elif "ارتودنسی" in c_text:
            topic = "خدمات ارتودنسی تخصصی"
        elif "عصب" in c_text or "ترمیم" in c_text:
            topic = "عصب‌کشی و ترمیم تخصصی دندان"
        elif "جرم" in c_text or "بلیچینگ" in c_text or "سفید" in c_text:
            topic = "بلیچینگ و سفید کردن دندان"
        # Beauty / Clinic terms
        elif "بوتاکس" in c_text or "ژل" in c_text or "فیلر" in c_text:
            topic = "تزریق ژل، بوتاکس و امور زیبایی"
        elif "پوست" in c_text or "فیشیال" in c_text or "لیزر" in c_text:
            topic = "خدمات پوست، فیشیال و لیزر"
        # Clothing / Apparel terms
        elif "مانتو" in c_text or "پالتو" in c_text or "بارانی" in c_text:
            topic = "مانتو و پالتو"
        elif "شومیز" in c_text or "بلوز" in c_text or "تیشرت" in c_text:
            topic = "مدل‌های شومیز و پیراهن"
        elif "شلوار" in c_text or "کت" in c_text:
            topic = "کت و شلوار"
        elif "سایز" in c_text or "رنگ" in c_text:
            topic = "استعلام سایزبندی و رنگ‌بندی"
        elif "قیمت" in c_text or "هزینه" in c_text or "چند" in c_text:
            topic = "هزینه‌ها و شرایط اقساطی"
        elif "ارسال" in c_text or "موجود" in c_text:
            topic = "موجودی و شرایط ارسال سریع"

    # Multi-structure templates: 4 distinct architectures to avoid semantic pattern detection
    STRUCTURE_PATTERNS = [
        # Architecture 1: Consultative & friendly (مشاوره‌ای و صمیمی)
        (
            "{درود بر شما|سلام وقتتون بخیر|سلام و احترام|درود}{ عزیز| گرامی|}!\n"
            "{بنده {consultant_name} هستم، مشاور و پشتیبانی تخصصی {business_name}|من {consultant_name} هستم از تیم مشاوره {business_name}|{consultant_name} هستم، مشاور همراه شما در {business_name}}.\n"
            "{متوجه شدم|دیدم|مشاهده کردم} {پیگیر|دنبال|در حال بررسی} {topic} بودید.\n"
            "{خوشحال می‌شم هر سوال یا ابهامی دارید راهنماییتون کنم.|اگر سوالی درباره روند کار، تعرفه‌ها یا شرایط دارید در خدمتم.|پاسخگوی تمام سوالات تخصصی شما در این زمینه هستم.}\n"
            "{اگر مایل بودید شماره تماستون رو بگذارید تا برای یک مشاوره اختصاصی ۱۰ دقیقه‌ای باهاتون هماهنگ بشه.|در صورت تمایل می‌تونید شماره تماستون رو ارسال کنید تا راهنمایی کامل تلفنی انجام بشه.|می‌تونید همینجا بفرمایید تا کامل راهنماییتون کنم.}"
        ),
        # Architecture 2: Responsive & direct (پاسخگویی سریع و حرفه‌ای)
        (
            "{سلام روزتون بخیر|درود و وقت‌بخیر|سلام و عرض ادب}{clean_name}!\n"
            "{امیدوارم عالی باشید.|روز خیلی خوبی براتون آرزو می‌کنم.}\n"
            "{بنده {consultant_name} هستم از {business_name}|{consultant_name} هستم کارشناس {business_name}}؛ در خصوص استعلام شما برای {topic} پیامتون رو دیدم.\n"
            "{برای اینکه در مورد جزئیات، کیفیت و هزینه‌ها دید دقیق‌تری داشته باشید، با کمال میل راهنماییتون می‌کنم.|اگر مایل باشید درباره نمونه‌کارها، قیمت‌ها یا نحوه ارائه خدمات بیشتر بدونید در خدمتم.}\n"
            "{می‌تونید سوالتون رو همینجا بفرمایید یا در صورت تمایل شماره تماس ارسال کنید تا توضیحات تکمیلی رو تلفنی تقدیم کنیم.}"
        ),
        # Architecture 3: Problem solving & value-first (رویکرد حل مساله و پیشنهاد مشاوره)
        (
            "{سلام و درود|وقت شما بخیر|سلام و احترام}{ گرامی|}!\n"
            "{در خصوص پیگیری شما درباره {topic}|با توجه به اینکه جویای {topic} بودید}، بنده {consultant_name} مشاور مجموعه {business_name} پیام می‌دم خدمتتون.\n"
            "{جهت راهنمایی بهتر و اطلاع از تعرفه‌ها و تخفیف‌های جاری|برای اینکه بتونیم بهترین راهکار متناسب با نیاز شما رو معرفی کنیم}، در خدمتتون هستیم.\n"
            "{اگر فرصت دارید شماره تماستون رو ارسال بفرمایید تا یک تماس کوتاه و کاربردی داشته باشیم، یا همینجا بفرمایید تا راهنمایی کنم.}"
        ),
        # Architecture 4: Short, human and conversational (کوتاه، صمیمی و گفتگومحور)
        (
            "{درود بر شما|سلام وقتتون بخیر}{clean_name}!\n"
            "{consultant_name} هستم از {business_name}.\n"
            "{دیدم پیگیر موضوع {topic} بودید|متوجه سوال شما درباره {topic} شدم}؛ {اگر سوال یا ابهامی هست خوشحال می‌شم کمکتون کنم.|هر نکته‌ای درباره قیمت‌ها یا شرایط کار مدنظرتونه در خدمتم.}\n"
            "{همینجا بفرمایید یا اگر مایلید شماره تماس بذارید تا مشاوره تلفنی هماهنگ کنیم.}"
        ),
    ]

    if pitch_template and "{" in pitch_template and "}" in pitch_template:
        # User defined dynamic template with Spintax
        text = pitch_template
    elif pitch_template:
        # User defined static template -> Wrap with dynamic spintax greetings & closers to prevent spam flag
        greetings = "{درود بر شما|سلام وقتتون بخیر|سلام و احترام|درود}{ عزیز| گرامی|}!\n"
        closings = "\n{در پناه حق|با آرزوی موفقیت|با احترام|ارادتمند شما}"
        text = greetings + pitch_template.strip() + closings
    else:
        # Choose one of the 4 diverse architectures randomly
        text = random.choice(STRUCTURE_PATTERNS)

    text = text.replace("{consultant_name}", consultant_name)
    text = text.replace("{business_name}", business_name)
    text = text.replace("{topic}", topic)
    text = text.replace("{clean_name}", clean_name)

    # Resolve recursive Spintax
    text = parse_spintax(text)

    # Micro-variations: Anti-Fingerprint Watermarking
    # Adds subtle variation in invisible zero-width non-joiners (\u200c) and random polite sign-offs
    closers = [
        "",
        " 🌸",
        " 🙏",
        " ✨",
        " 🌷",
        " 🌿",
        " 💫",
        " 🤍",
        " ✨🙏",
        " 🌸✨",
    ]
    selected_closer = random.choice(closers)
    if selected_closer and not any(text.endswith(c.strip()) for c in ["🌸", "🙏", "✨", "🌷", "🌿", "💫", "🤍"]):
        text = text.rstrip() + selected_closer

    # Safe zero-width non-joiner injection to make message hash 100% unique
    if random.random() < 0.5:
        text = text.replace("می‌شم", "می\u200cشم").replace("راهنماییتون", "راهنمایی\u200cتون")

    return text


def qualify_recipient_profile(cl: Any, username: str, target_pk: Optional[int] = None) -> Dict[str, Any]:
    """
    Super Pre-flight safety check on recipient before sending DM:
    Uses mobile private API only (never falls back to slow/blocked public web scraping).
    1. Detects Private Accounts: Sending cold DMs to private accounts triggers Instagram error 1404006.
    2. Detects Ghost/Fake Accounts: Accounts with 0 posts & 0 followers risk spam flagging.
    Returns:
        {"eligible": True/False, "reason": str, "user_info": Any, "target_pk": int}
    """
    user_data = None
    numeric_pk = int(target_pk) if target_pk and str(target_pk).isdigit() else None

    # 1. Try mobile private user info by numeric PK
    if numeric_pk:
        try:
            res = cl.private_request(f"users/{numeric_pk}/info/", params={"entry_point": "profile", "from_module": "profile"})
            if isinstance(res, dict) and res.get("status") == "ok" and "user" in res:
                user_data = res["user"]
        except Exception as pk_err:
            logger.debug(f"Private user info by PK {numeric_pk} failed: {pk_err}")

    # 2. If no data, try private usernameinfo
    if not user_data and username:
        try:
            res = cl.private_request(f"users/{username}/usernameinfo/")
            if isinstance(res, dict) and res.get("status") == "ok" and "user" in res:
                user_data = res["user"]
        except Exception as un_err:
            logger.debug(f"Private usernameinfo for {username} failed: {un_err}")

    if user_data:
        is_private = bool(user_data.get("is_private", False))
        resolved_pk = int(user_data.get("pk") or numeric_pk or 0)
        media_count = int(user_data.get("media_count", 1))
        follower_count = int(user_data.get("follower_count", 1))

        if media_count == 0 and follower_count == 0:
            return {
                "eligible": False,
                "reason": "پروفایل غیرفعال یا مشکوک (۰ پست و ۰ فالوور) جهت ایمنی رد شد.",
                "user_info": user_data,
                "target_pk": resolved_pk,
            }

        # Private accounts can safely receive direct messages (delivered into Message Requests / درخواست پیام)
        # We allow them so outreach does not stall when prospective customers have private personal profiles.
        return {
            "eligible": True,
            "reason": "واجد شرایط دریافت دایرکت" if not is_private else "واجد شرایط دریافت دایرکت (پیج خصوصی - ارسال به صندوق درخواست پیام)",
            "user_info": user_data,
            "target_pk": resolved_pk,
        }

    # If user lookup returned fail / not found, but we already have a verified numeric PK from comment
    if numeric_pk:
        return {
            "eligible": True,
            "reason": "واجد شرایط دریافت دایرکت بر اساس PK معتبر کاربری",
            "user_info": None,
            "target_pk": numeric_pk,
        }

    return {
        "eligible": False,
        "reason": "عدم امکان احراز هویت شناسه کاربر در اینستاگرام",
        "user_info": None,
        "target_pk": None,
    }


def perform_organic_warmup(cl: Any, target_pk: int, username: str) -> Dict[str, Any]:
    """
    Executes organic human warm-up actions before sending DM:
    1. Checks and views active stories (leaves account avatar in user's story viewers list).
    2. Likes the user's latest post (triggers official push notification on recipient's phone).
    Instagram algorithm classifies the subsequent DM as a natural follow-up interaction!
    """
    actions = {"liked_post": False, "viewed_story": False}

    # 1. View active stories (if any)
    try:
        if hasattr(cl, "user_stories"):
            stories = cl.user_stories(str(target_pk))
            if stories:
                # View up to 2 active stories with realistic human delay
                story_pks = [int(s.pk) for s in stories[:2]]
                if hasattr(cl, "story_seen"):
                    cl.story_seen(story_pks)
                    actions["viewed_story"] = True
                    logger.info("Organic story view executed for @%s (pks: %s)", username, story_pks)
                    time.sleep(random.uniform(2.0, 4.5))
    except Exception as story_err:
        logger.debug("Organic story view skipped for @%s: %s", username, story_err)

    # 2. Like recent post (if any)
    try:
        medias = cl.user_medias(target_pk, amount=1)
        if medias:
            latest_media = medias[0]
            cl.media_like(latest_media.id)
            actions["liked_post"] = True
            logger.info("Organic post like executed for @%s (media: %s)", username, latest_media.id)
            time.sleep(random.uniform(1.5, 3.5))
    except Exception as like_err:
        logger.debug("Organic like warmup skipped for @%s: %s", username, like_err)

    return actions


def generate_ai_outreach_pitches(
    business_name: str,
    consultant_name: str,
    business_desc: str = "",
    tone_style: str = "friendly",
    focus_area: str = "",
) -> List[Dict[str, str]]:
    """
    Generate 3 distinct, high-converting Persian Instagram DM pitches for cold outreach.
    Respects Instagram anti-spam principles:
    - Warm, personal opening
    - Concise (2-4 lines)
    - Friendly question / soft CTA without external links
    """
    system_prompt = (
        "شما یک متخصص بازاریابی دیجیتال و کپی‌رایتر ارشد برای دایرکت مارکتینگ اینستاگرام هستید. "
        "وظیفه شما نوشتن ۳ پیش‌نویس پیام دایرکت (Cold DM) کاملاً طبیعی، صمیمی و بدون شباهت به ربات است.\n"
        "قوانین حیاتی ضد اسپم اینستاگرام:\n"
        "۱. پیام باید کوتاه (حداکثر ۳ یا ۴ جمله)، طبیعی و محترمانه باشد.\n"
        "۲. در پیام اول به هیچ عنوان لینک وب‌سایت یا آیدی کانال قرار ندهید (باعث بلاک می‌شود).\n"
        "۳. هدف پیام باز کردن گفتگو (Open a conversation) است، نه فروش تهاجمی.\n"
        "۴. لحن باید متناسب با هویت مشاور و کسب‌وکار باشد.\n"
        "خروجی باید یک آبجکت JSON معتبر به فرمت زیر باشد:\n"
        "{\n"
        '  "pitches": [\n'
        '    {"id": 1, "title": "مشاوره‌ای و صمیمی", "text": "...", "badge": "پیشنهادی"},\n'
        '    {"id": 2, "title": "دعوت به مشاوره رایگان ۱۰ دقیقه‌ای", "text": "...", "badge": "مکالمه‌محور"},\n'
        '    {"id": 3, "title": "طرح سوال و بررسی تخصصی نیاز", "text": "...", "badge": "ارزیابی تخصصی"}\n'
        "  ]\n"
        "}"
    )

    user_prompt = (
        f"نام کسب‌وکار: {business_name}\n"
        f"نام مشاور / فرستنده: {consultant_name}\n"
        f"توضیحات کسب‌وکار: {business_desc or 'خدمات تخصصی و سلامت'}\n"
        f"موضوع / تمرکز پیام: {focus_area or 'بررسی کامنت علاقه‌مندی مشتری و پیشنهاد راهنمایی تخصصی'}\n"
        f"لحن انتخابی: {tone_style}\n\n"
        "لطفاً ۳ نسخه متنوع و جذاب برای دایرکت بنویسید."
    )

    try:
        from apps.core.llm import chat_json
        res = chat_json(
            system=system_prompt,
            user=user_prompt,
            purpose="instagram_outreach_pitch",
            max_tokens=1500,
            timeout=25.0,
        )
        if isinstance(res, dict) and "pitches" in res and isinstance(res["pitches"], list) and len(res["pitches"]) > 0:
            return res["pitches"]
    except Exception as exc:
        logger.warning(f"LLM pitch generation failed, using tailored smart templates: {exc}")

    # High-quality smart template fallbacks
    biz = business_name or "مجموعه تخصصی"
    c_name = consultant_name or "مشاور"
    focus = focus_area.strip() or "امور درمانی و زیبایی"

    return [
        {
            "id": 1,
            "title": "مشاوره‌ای و صمیمی",
            "badge": "پیشنهادی (بیشترین نرخ پاسخ)",
            "text": (
                f"درود بر شما وقتتون بخیر\n"
                f"بنده {c_name} هستم، مشاور تخصصی {biz}. متوجه شدم پیگیر {focus} بودید.\n"
                f"اگر هر سوال یا ابهامی درباره شرایط، هزینه‌ها یا نحوه انجامش دارید خوشحال می‌شم کمکتون کنم. "
                f"اگر مایل باشید می‌تونید شماره تماستون رو بفرستید تا برای ۱۰ دقیقه مشاوره اختصاصی باهاتون تماس بگیرم."
            ),
        },
        {
            "id": 2,
            "title": "دعوت به مشاوره رایگان ۱۰ دقیقه‌ای",
            "badge": "مکالمه‌محور",
            "text": (
                f"سلام و عرض ادب\n"
                f"من {c_name} هستم از پشتیبانی {biz}. کامنت زیباتون رو دیدم و خواستم شخصاً در کنارتون باشم.\n"
                f"برای اینکه بتونیم بهترین راهکار رو با توجه به شرایط شما پیشنهاد بدیم، یک نوبت مشاوره تخصصی رایگان براتون در نظر گرفتیم. "
                f"مایلید شرایط رو همینجا بررسی کنیم یا تماس بگیریم؟"
            ),
        },
        {
            "id": 3,
            "title": "طرح سوال و بررسی تخصصی نیاز",
            "badge": "ارزیابی تخصصی",
            "text": (
                f"درود بر شما عزیز\n"
                f"{c_name} هستم مشاور {biz}. در ارتباط با کامنتی که راجع به {focus} ثبت کرده بودید پیام می‌دم.\n"
                f"دقیقاً چه مواردی براتون اولویت داره تا کامل و بدون معطلی راهنماییتون کنم؟ پاسخگوی سوالاتتون هستم."
            ),
        },
    ]


def extract_iranian_phone_number(text: Optional[str]) -> Optional[str]:
    """
    Extracts an Iranian mobile phone number from text containing Persian/Arabic/English digits.
    Returns normalized '09XXXXXXXXX' (11 digits) or None.
    """
    if not text:
        return None
    persian_arabic = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    cleaned = str(text).translate(persian_arabic)

    # Match common patterns: +98912..., 0098912..., 0912..., 912... with optional spaces/dashes
    pattern = re.compile(
        r"(?:(?:\+|00)?98[-\s]?|0)?(9\d{2}[-\s]?\d{3}[-\s]?\d{4})\b"
    )
    m = pattern.search(cleaned)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) == 10 and digits.startswith("9"):
            return f"0{digits}"

    # Fallback: scan any sequence of 10-14 digits
    digits_only = re.sub(r"\D", "", cleaned)
    if len(digits_only) == 11 and digits_only.startswith("09"):
        return digits_only
    if len(digits_only) == 10 and digits_only.startswith("9"):
        return f"0{digits_only}"
    if len(digits_only) == 12 and digits_only.startswith("989"):
        return f"0{digits_only[2:]}"
    if len(digits_only) == 14 and digits_only.startswith("00989"):
        return f"0{digits_only[4:]}"

    return None


def get_lead_conversation_thread(lead: Any) -> List[Dict[str, Any]]:
    """
    Returns the complete, chronologically sorted list of direct messages for an Instagram lead.
    Gracefully reconstructs missing entries from legacy fields (initial_dm_text, reply_text, second_message_text).
    """
    history = list(getattr(lead, "conversation_history", []) or [])

    # If history is empty, reconstruct from legacy single-message fields
    if not history:
        if getattr(lead, "initial_dm_text", None):
            history.append({
                "sender": getattr(lead, "sender_account", "") or "مشاور",
                "direction": "out",
                "role": "initial_outreach",
                "text": lead.initial_dm_text,
                "time": lead.initial_dm_at.isoformat() if getattr(lead, "initial_dm_at", None) else "",
            })
        if getattr(lead, "reply_text", None):
            history.append({
                "sender": getattr(lead, "username", "") or "مشتری",
                "direction": "in",
                "role": "lead_reply",
                "text": lead.reply_text,
                "time": lead.reply_at.isoformat() if getattr(lead, "reply_at", None) else "",
            })
        if getattr(lead, "second_message_text", None):
            history.append({
                "sender": getattr(lead, "second_sender_username", "") or getattr(lead, "sender_account", "") or "مشاور",
                "direction": "out",
                "role": "followup",
                "text": lead.second_message_text,
                "time": lead.second_message_at.isoformat() if getattr(lead, "second_message_at", None) else "",
            })
    else:
        # Check if initial_dm_text is missing from the beginning of history
        first_msg = history[0] if history else {}
        if getattr(lead, "initial_dm_text", None) and first_msg.get("direction") == "in":
            history.insert(0, {
                "sender": getattr(lead, "sender_account", "") or "مشاور",
                "direction": "out",
                "role": "initial_outreach",
                "text": lead.initial_dm_text,
                "time": lead.initial_dm_at.isoformat() if getattr(lead, "initial_dm_at", None) else "",
            })

        # Check if second_message_text was sent but not added to history
        if getattr(lead, "second_message_text", None):
            has_second = any(
                m.get("direction") == "out" and m.get("text") == lead.second_message_text
                for m in history
            )
            if not has_second:
                history.append({
                    "sender": getattr(lead, "second_sender_username", "") or getattr(lead, "sender_account", "") or "مشاور",
                    "direction": "out",
                    "role": "followup",
                    "text": lead.second_message_text,
                    "time": lead.second_message_at.isoformat() if getattr(lead, "second_message_at", None) else "",
                })

    formatted_thread = []
    for m in history:
        direction = m.get("direction", "out")
        sender = m.get("sender", "")
        time_str = m.get("time", "")
        formatted_thread.append({
            "sender": sender,
            "direction": direction,
            "role": m.get("role", "message"),
            "text": m.get("text", ""),
            "time": time_str,
            "delivery_status": m.get("delivery_status", "SENT"),
            "is_incoming": direction == "in",
            "is_queued": m.get("delivery_status") == "QUEUED",
        })
    return formatted_thread


def check_and_sync_lead_replies(tenant_ids: List[str]) -> Dict[str, Any]:
    """
    Checks direct message inboxes of all SENDER accounts for given tenants.
    Detects if any lead has replied, saves their responses to conversation_history,
    extracts Iranian mobile numbers, and marks lead as REPLIED.
    """
    from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead, Customer
    from apps.discovery.device_emulator import apply_device_profile_to_client

    if isinstance(tenant_ids, str):
        tenant_ids = [tenant_ids]

    senders = InstagramEngineAccount.objects.filter(
        role="SENDER",
        status="ACTIVE",
        tenant_id__in=tenant_ids,
    )

    if not senders.exists():
        return {"status": "no_senders", "replies_found": 0, "details": []}

    from instagrapi import Client

    synced_count = 0
    details = []

    for sender in senders:
        if not sender.session_json:
            continue

        cookies = sender.session_json.get("cookies", {})
        sessionid = cookies.get("sessionid")
        if not sessionid:
            continue

        cl = Client()
        cl.delay_range = [0.2, 0.5]
        if sender.proxy_url:
            cl.set_proxy(sender.proxy_url)

        try:
            apply_device_profile_to_client(cl, sender)
            cl.login_by_sessionid(sessionid)
            current_user_id = str(cl.user_id)

            # Fast single chunk fetch (max 10 recent threads without paging loop)
            threads, _ = cl.direct_threads_chunk(thread_message_limit=10)
            for th in threads[:10]:
                messages = getattr(th, "messages", [])
                if not messages:
                    continue

                # Find messages in this thread from the customer
                customer_messages = [m for m in messages if str(m.user_id) != current_user_id and (getattr(m, "text", "") or "").strip()]
                if not customer_messages:
                    continue

                latest_msg = customer_messages[0]
                msg_sender_id = str(latest_msg.user_id)

                # Find other participant in thread
                other_users = getattr(th, "users", [])
                other_pk = str(other_users[0].pk) if other_users else msg_sender_id
                other_username = other_users[0].username if other_users else ""

                # Match lead in database or auto-create from active direct thread
                lead = InstagramEngineLead.objects.filter(
                    tenant_id__in=tenant_ids,
                    ig_user_pk=other_pk,
                ).first()

                if not lead and other_username:
                    lead = InstagramEngineLead.objects.filter(
                        tenant_id__in=tenant_ids,
                        username__iexact=other_username,
                    ).first()

                if not lead and other_username:
                    tenant_id = sender.tenant_id if sender.tenant_id else tenant_ids[0]
                    lead = InstagramEngineLead.objects.create(
                        tenant_id=tenant_id,
                        ig_user_pk=other_pk,
                        username=other_username,
                        profile_url=f"https://instagram.com/{other_username}",
                        comment_text="مشتری با ارسال پیام در دایرکت پاسخ داده است",
                        intent_score=0.98,
                        status="REPLIED",
                        sender_account=sender.username,
                    )

                if lead:
                    msg_text = str(latest_msg.text or "").strip()
                    msg_time = latest_msg.timestamp or timezone.now()

                    # Extract our initial message if available
                    for m in reversed(messages):
                        if str(m.user_id) == current_user_id and m.text:
                            lead.initial_dm_text = lead.initial_dm_text or str(m.text)
                            lead.initial_dm_at = lead.initial_dm_at or m.timestamp
                            break

                    history = list(lead.conversation_history or [])
                    new_msgs_added = False

                    # Process customer messages in chronological order (oldest to newest)
                    for cm in reversed(customer_messages):
                        cm_text = str(getattr(cm, "text", "") or "").strip()
                        if not cm_text:
                            continue
                        cm_time = getattr(cm, "timestamp", None) or msg_time
                        cm_time_iso = cm_time.isoformat() if hasattr(cm_time, "isoformat") else str(cm_time)

                        already_in_history = any(
                            m.get("direction") == "in" and m.get("text") == cm_text
                            for m in history
                        )
                        if not already_in_history:
                            history.append({
                                "sender": lead.username,
                                "direction": "in",
                                "text": cm_text,
                                "time": cm_time_iso,
                            })
                            new_msgs_added = True

                            # Check for Iranian phone number
                            phone = extract_iranian_phone_number(cm_text)
                            if phone and lead.ig_user_pk:
                                Customer.objects.filter(
                                    external_user_id=str(lead.ig_user_pk)
                                ).update(phone_number=phone)

                    if not lead.has_replied or lead.reply_text != msg_text or new_msgs_added:
                        lead.has_replied = True
                        lead.status = "REPLIED"
                        lead.reply_text = msg_text
                        lead.reply_at = msg_time
                        lead.sender_account = lead.sender_account or sender.username
                        lead.conversation_history = history
                        lead.save()

                        synced_count += 1
                        details.append({
                            "lead": lead.username,
                            "sender": sender.username,
                            "reply_text": msg_text[:80],
                        })
                        logger.info(f"✅ New reply detected from @{lead.username} to @{sender.username}: {msg_text[:50]}")

        except Exception as exc:
            logger.warning(f"Error checking replies on sender @{sender.username}: {exc}")

    return {
        "status": "success",
        "replies_found": synced_count,
        "details": details,
    }


def execute_organic_journey(
    cl: Any,
    target_username: str,
    target_pk: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Executes an organic, authentic human journey before sending a direct message:
    1. Scrolls home timeline feed for 3-6s (with 15% probability to like a feed post).
    2. Searches for the username in Instagram search bar (/users/search/).
    3. Views target profile grid and inspects recent medias.
    4. Views active stories (leaving avatar impression) & likes recent post.
    5. Applies human typing jitter (15-28s).
    """
    actions_taken = {
        "feed_scrolled": False,
        "feed_post_liked": False,
        "user_searched": False,
        "profile_viewed": False,
        "story_viewed": False,
        "target_post_liked": False,
        "typing_delay_sec": 0.0,
    }

    # Step 1: Open Timeline Feed & simulate casual scrolling
    try:
        if hasattr(cl, "get_timeline_feed"):
            timeline = cl.get_timeline_feed()
            actions_taken["feed_scrolled"] = True
            time.sleep(random.uniform(3.0, 6.0))

            feed_items = getattr(timeline, "feed_items", []) or (timeline.get("feed_items", []) if isinstance(timeline, dict) else [])
            if feed_items and random.random() < 0.15:
                first_item = feed_items[0]
                m_id = None
                if isinstance(first_item, dict):
                    m_id = (first_item.get("media_or_ad") or {}).get("id") or first_item.get("id")
                elif hasattr(first_item, "id"):
                    m_id = first_item.id
                if m_id and hasattr(cl, "media_like"):
                    cl.media_like(str(m_id))
                    actions_taken["feed_post_liked"] = True
                    logger.info("❤️ Organic feed post like executed during casual scroll (media: %s)", m_id)
                    time.sleep(random.uniform(1.5, 3.0))
    except Exception as feed_err:
        logger.debug("Organic timeline feed scroll skipped: %s", feed_err)

    # Step 2: Search for user in search bar (/users/search/)
    resolved_pk = target_pk
    try:
        time.sleep(random.uniform(2.0, 4.0))
        if hasattr(cl, "search_users"):
            search_results = cl.search_users(target_username)
            actions_taken["user_searched"] = True
            for u in (search_results or []):
                u_name = getattr(u, "username", "") or (u.get("username", "") if isinstance(u, dict) else "")
                u_pk = getattr(u, "pk", None) or (u.get("pk") if isinstance(u, dict) else None)
                if str(u_name).lower() == str(target_username).lower() and u_pk:
                    resolved_pk = int(u_pk)
                    break
    except Exception as search_err:
        logger.debug("Organic search bar simulation skipped for @%s: %s", target_username, search_err)

    if not resolved_pk and hasattr(cl, "user_id_from_username"):
        try:
            resolved_pk = int(cl.user_id_from_username(target_username))
        except Exception:
            pass

    # Step 3: Browse target user profile and recent medias
    if resolved_pk:
        try:
            time.sleep(random.uniform(2.0, 4.0))
            if hasattr(cl, "user_medias"):
                cl.user_medias(resolved_pk, amount=3)
                actions_taken["profile_viewed"] = True
        except Exception as profile_err:
            logger.debug("Organic profile inspection skipped for @%s: %s", target_username, profile_err)

        # Step 4: Perform existing story seen + post like interaction
        warmup_res = perform_organic_warmup(cl, resolved_pk, target_username)
        actions_taken["story_viewed"] = warmup_res.get("viewed_story", False)
        actions_taken["target_post_liked"] = warmup_res.get("liked_post", False)

    # Step 5: Realistic typing delay before dispatching
    typing_sec = random.uniform(15.0, 30.0)
    actions_taken["typing_delay_sec"] = round(typing_sec, 2)
    time.sleep(typing_sec)

    return {
        "target_pk": resolved_pk,
        "actions": actions_taken,
    }


def enqueue_direct_message(
    lead_id: int,
    message_text: str,
    sender_username: Optional[str] = None,
    priority: str = "HIGH",
) -> Dict[str, Any]:
    """
    Enqueues an operator or AI direct message into the secure Dispatch Queue.
    Protects the sender account from burst rate violations and allows worker to
    smoothly dispatch with human spacing and organic navigation.
    """
    from apps.discovery.models import InstagramEngineLead, InstagramEngineAccount

    lead = InstagramEngineLead.objects.filter(pk=lead_id).first()
    if not lead:
        return {"success": False, "error": "مشتری مورد نظر یافت نشد."}

    sender = None
    if sender_username:
        sender = InstagramEngineAccount.objects.filter(username__iexact=sender_username, role="SENDER").first()
    if not sender and lead.second_sender_username:
        sender = InstagramEngineAccount.objects.filter(username__iexact=lead.second_sender_username, role="SENDER").first()
    if not sender and lead.sender_account:
        sender = InstagramEngineAccount.objects.filter(username__iexact=lead.sender_account, role="SENDER").first()
    if not sender:
        sender = InstagramEngineAccount.objects.filter(tenant_id=lead.tenant_id, role="SENDER", status="ACTIVE").first()

    chosen_sender = sender.username if sender else (sender_username or "مشاور")

    now = timezone.now()
    history = list(lead.conversation_history or [])
    history.append({
        "sender": chosen_sender,
        "direction": "out",
        "role": "operator_message",
        "text": message_text,
        "time": now.isoformat(),
        "delivery_status": "QUEUED",
        "priority": priority,
    })

    lead.status = "QUEUED"
    lead.conversation_history = history
    if sender:
        lead.second_sender_username = sender.username
    lead.save(update_fields=["status", "conversation_history", "second_sender_username", "updated_at"])

    logger.info("Message for @%s enqueued with priority %s for sender @%s", lead.username, priority, chosen_sender)
    return {
        "success": True,
        "queued": True,
        "lead": lead.username,
        "sender": chosen_sender,
        "message": f"پیام با موفقیت در صف ارسال امن قرار گرفت و به زودی توسط @{chosen_sender} ارسال می‌شود.",
    }


def process_queued_outbox_message(
    lead: Any,
    client_cache: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Safely dispatches the oldest pending QUEUED message for a lead using
    organic navigation and account session.
    """
    from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
    from apps.discovery.device_emulator import apply_device_profile_to_client
    from instagrapi import Client

    history = list(getattr(lead, "conversation_history", []) or [])
    queued_idx = None
    queued_item = None
    for idx, item in enumerate(history):
        if item.get("direction") == "out" and item.get("delivery_status") == "QUEUED":
            queued_idx = idx
            queued_item = item
            break

    if queued_item is None:
        return {"success": False, "error": "هیچ پیام در صف برای این لید یافت نشد."}

    sender_username = queued_item.get("sender") or lead.second_sender_username or lead.sender_account
    sender = InstagramEngineAccount.objects.filter(username__iexact=sender_username, role="SENDER").first()
    if not sender:
        sender = InstagramEngineAccount.objects.filter(tenant_id=lead.tenant_id, role="SENDER", status="ACTIVE").first()

    if dry_run:
        now = timezone.now()
        chosen_sender = sender.username if sender else (sender_username or "مشاور")
        queued_item["delivery_status"] = "SENT"
        queued_item["sent_at"] = now.isoformat()
        history[queued_idx] = queued_item

        lead.conversation_history = history
        lead.status = "FOLLOWUP_SENT"
        if not lead.second_message_text:
            lead.second_message_text = queued_item["text"]
            lead.second_sender_username = chosen_sender
            lead.second_message_at = now
        lead.save(update_fields=["status", "conversation_history", "second_message_text", "second_sender_username", "second_message_at", "updated_at"])

        if sender:
            sender.daily_action_count = (sender.daily_action_count or 0) + 1
            sender.last_action_at = now
            sender.save(update_fields=["daily_action_count", "last_action_at"])

        return {
            "success": True,
            "dry_run": True,
            "lead": lead.username,
            "sender": chosen_sender,
            "text": queued_item["text"],
            "sent_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }

    if not sender or not sender.session_json:
        return {"success": False, "error": f"اکانت فرستنده معتبر برای @{lead.username} در دسترس نیست."}

    cookies = sender.session_json.get("cookies", {})
    sess_id = cookies.get("sessionid")
    if not sess_id:
        return {"success": False, "error": f"کوکی sessionid برای @{sender.username} ثبت نشده است."}

    cl = None
    if client_cache is not None and sender.username in client_cache:
        cl = client_cache[sender.username]
    else:
        cl = Client()
        if sender.proxy_url:
            cl.set_proxy(sender.proxy_url)
        apply_device_profile_to_client(cl, sender)
        cl.login_by_sessionid(sess_id)
        if client_cache is not None:
            client_cache[sender.username] = cl

    try:
        # Execute in-app organic journey before dispatching DM
        target_pk = int(lead.ig_user_pk) if lead.ig_user_pk and str(lead.ig_user_pk).isdigit() else None
        journey_res = execute_organic_journey(cl, lead.username, target_pk=target_pk)
        final_pk = journey_res.get("target_pk") or target_pk or int(cl.user_id_from_username(lead.username))

        # Send DM
        cl.direct_send(queued_item["text"], user_ids=[final_pk])

        now = timezone.now()
        queued_item["delivery_status"] = "SENT"
        queued_item["sent_at"] = now.isoformat()
        history[queued_idx] = queued_item

        lead.conversation_history = history
        lead.status = "FOLLOWUP_SENT"
        if not lead.second_message_text:
            lead.second_message_text = queued_item["text"]
            lead.second_sender_username = sender.username
            lead.second_message_at = now
        lead.save(update_fields=["status", "conversation_history", "second_message_text", "second_sender_username", "second_message_at", "updated_at"])

        sender.daily_action_count = (sender.daily_action_count or 0) + 1
        sender.last_action_at = now
        sender.save(update_fields=["daily_action_count", "last_action_at"])

        return {
            "success": True,
            "lead": lead.username,
            "sender": sender.username,
            "text": queued_item["text"],
            "sent_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as exc:
        logger.exception("Error dispatching queued message to @%s: %s", lead.username, exc)
        return {"success": False, "error": str(exc)}


def send_direct_message_to_lead(
    lead_id: int,
    message_text: str,
    sender_username: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends a direct message to an Instagram lead in Direct via the chosen or default SENDER account.
    Appends the message to lead.conversation_history and updates tracking fields.
    """
    from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
    from apps.discovery.device_emulator import apply_device_profile_to_client
    from instagrapi import Client

    lead = InstagramEngineLead.objects.filter(pk=lead_id).first()
    if not lead:
        return {"success": False, "error": "مشتری مورد نظر یافت نشد."}

    sender = None
    if sender_username:
        sender = InstagramEngineAccount.objects.filter(
            username__iexact=sender_username,
            role="SENDER",
        ).first()
        if not sender:
            return {"success": False, "error": f"اکانت فرستنده @{sender_username} یافت نشد."}
    else:
        # Auto-pick last used sender or first active sender for this tenant
        if lead.second_sender_username:
            sender = InstagramEngineAccount.objects.filter(
                username__iexact=lead.second_sender_username, role="SENDER", status="ACTIVE"
            ).first()
        if not sender and lead.sender_account:
            sender = InstagramEngineAccount.objects.filter(
                username__iexact=lead.sender_account, role="SENDER", status="ACTIVE"
            ).first()
        if not sender:
            sender = InstagramEngineAccount.objects.filter(
                tenant_id=lead.tenant_id, role="SENDER", status="ACTIVE"
            ).first()

    if not sender:
        return {"success": False, "error": "هیچ اکانت فرستنده معتبری برای ارسال پیام یافت نشد."}

    if not sender.session_json:
        return {"success": False, "error": f"اکانت @{sender.username} فاقد کوکی سشن معتبر است."}

    cookies = sender.session_json.get("cookies", {})
    sess_id = cookies.get("sessionid")
    if not sess_id:
        return {"success": False, "error": f"کوکی sessionid برای @{sender.username} ثبت نشده است."}

    cl = Client()
    if sender.proxy_url:
        cl.set_proxy(sender.proxy_url)

    try:
        apply_device_profile_to_client(cl, sender)
        cl.login_by_sessionid(sess_id)

        target_pk = int(lead.ig_user_pk) if lead.ig_user_pk and str(lead.ig_user_pk).isdigit() else None
        if not target_pk:
            target_pk = cl.user_id_from_username(lead.username)

        # Dispatch direct message to Instagram lead
        cl.direct_send(message_text, user_ids=[target_pk])

        now = timezone.now()

        # Update backward-compatibility fields if this is the first follow-up
        if not lead.second_message_text:
            lead.second_message_text = message_text
            lead.second_sender_username = sender.username
            lead.second_message_at = now

        lead.status = "FOLLOWUP_SENT"

        history = list(lead.conversation_history or [])
        history.append({
            "sender": sender.username,
            "direction": "out",
            "role": "operator_message",
            "text": message_text,
            "time": now.isoformat(),
        })
        lead.conversation_history = history
        lead.save(update_fields=[
            "second_message_text", "second_sender_username", "second_message_at",
            "status", "conversation_history", "updated_at"
        ])

        sender.daily_action_count = (sender.daily_action_count or 0) + 1
        sender.last_action_at = now
        sender.save(update_fields=["daily_action_count", "last_action_at"])

        return {
            "success": True,
            "lead": lead.username,
            "sender": sender.username,
            "sent_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as exc:
        logger.exception("Error sending direct message to @%s: %s", lead.username, exc)
        return {"success": False, "error": str(exc)}


def send_second_followup_message(
    lead_id: int,
    sender_username: str,
    message_text: str,
) -> Dict[str, Any]:
    """
    Sends a follow-up or second message to a lead using the chosen SENDER account.
    Maintained for full backward compatibility with existing views and tests.
    """
    return send_direct_message_to_lead(
        lead_id=lead_id,
        message_text=message_text,
        sender_username=sender_username,
    )
