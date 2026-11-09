"""DM texts: spintax, the varied cold-DM builder, AI pitch suggestions, and phone numbers found in replies."""
import logging
import random
import re
from typing import Dict, Any, List, Optional

logger = logging.getLogger("instagram_outreach")


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


_SPINTAX_GROUP_RE = re.compile(r"\{[^{}]*\|[^{}]*\}")


_LEADING_GREETING_RE = re.compile(r"^(?:درود بر شما|سلام وقتتون بخیر|سلام و احترام|سلام و درود|درود|سلام)[\s!،,.]*")


STATIC_TEMPLATE_GREETING = "{درود بر شما|سلام وقتتون بخیر|سلام و احترام|درود}{ عزیز| گرامی|}!"


def build_bulletproof_message(
    pitch_template: str = "",
    lead: Any = None,
    consultant_name: str = "مشاور",
    business_name: str = "کلینیک تخصصی دندانپزشکی",
) -> str:
    """
    Creates a 100% unique, anti-fingerprint DM using Spintax + dynamic context-aware personalization.
    Tailors the message directly to:
    1. The specific comment the user left (e.g. installment question, procedure inquiry, direct CTA).
    2. The specific post topic / caption context (composite, laminate, implant, ortho, etc.).
    3. Strictly ZERO EMOJIS, zero forbidden characters/links to prevent Meta spam blocks.
    """
    raw_name = (getattr(lead, "full_name", None) or getattr(lead, "username", "")).strip()
    clean_name = f" {raw_name}" if raw_name and not raw_name.startswith("@") and len(raw_name) < 25 else ""

    # 1. Determine dental service topic from comment and post caption
    combined_context = ""
    comment_val = getattr(lead, "comment_text", None)
    if isinstance(comment_val, str):
        combined_context += " " + comment_val.lower()
    caption_val = getattr(lead, "post_caption_snippet", None)
    if isinstance(caption_val, str):
        combined_context += " " + caption_val.lower()

    if "کامپوزیت" in combined_context:
        topic = "کامپوزیت ونیر و اصلاح طرح لبخند"
    elif "لمینت" in combined_context:
        topic = "لمینت سرامیکی و امور زیبایی دندان"
    elif "ایمپلنت" in combined_context or "کاشت" in combined_context:
        topic = "کاشت تخصصی ایمپلنت دندان"
    elif "ارتودنسی" in combined_context or "سیم کشی" in combined_context:
        topic = "خدمات ارتودنسی ثابت و نامرئی"
    elif "عصب کشی" in combined_context or "ریشه" in combined_context:
        topic = "عصب‌کشی و درمان ریشه دندان"
    elif "جراحی" in combined_context or "عقل" in combined_context:
        topic = "جراحی دندان عقل و لثه"
    elif "ترمیم" in combined_context or "پر کردن" in combined_context or "روکش" in combined_context:
        topic = "ترمیم، پر کردن و روکش دندان"
    elif "جرم" in combined_context or "بلیچینگ" in combined_context or "سفید" in combined_context:
        topic = "بلیچینگ و سفید کردن دندان"
    elif "بوتاکس" in combined_context or "فیلر" in combined_context or "ژل" in combined_context:
        topic = "تزریق ژل، بوتاکس و امور زیبایی"
    elif "پوست" in combined_context or "فیشیال" in combined_context or "لیزر" in combined_context:
        topic = "خدمات پوست، فیشیال و لیزر"
    else:
        topic = "خدمات تخصصی دندان‌پزشکی و زیبایی"

    # 2. Determine contextual response based on comment intent
    c_text = (getattr(lead, "comment_text", "") or "").lower()

    # Case A: User asked about installments or financial terms
    is_installment_inquiry = any(w in c_text for w in ["اقساط", "قسط", "چک", "تخفیف", "بیمه"])
    # Case B: User asked about procedure, duration, how it works, or pain
    is_procedure_inquiry = any(w in c_text for w in ["چطوری", "چطور", "نحوه", "چگونه", "چند جلسه", "درد", "طول", "مراحل"])
    # Case C: Direct short trigger or price inquiry
    is_price_inquiry = any(w in c_text for w in ["قیمت", "هزینه", "چند", "چنده", "دایرکت", "کامنت", "ارسال", "شرایط"])

    if pitch_template and _SPINTAX_GROUP_RE.search(pitch_template):
        # Template already carries its own Spintax variations
        text = pitch_template
    elif pitch_template:
        # Static (approved) template: keep the body as-is but vary the opening greeting,
        # otherwise every DM from this sender would be byte-identical (spam-classifier risk).
        body = _LEADING_GREETING_RE.sub("", pitch_template.strip(), count=1)
        text = STATIC_TEMPLATE_GREETING + "\n" + body
    elif is_installment_inquiry:
        text = (
            "{درود بر شما|سلام وقتتون بخیر|سلام و احترام|درود}{ عزیز|}!\n"
            "بنده {consultant_name} هستم، مشاور تخصصی {business_name}.\n"
            "{پیرو سوال شما در کامنت درباره شرایط اقساط و هزینه‌ها|درباره سوالتون پیرامون پرداخت اقساطی}: "
            "بله، تمامی خدمات {topic} با شرایط اقساطی منعطف، بدون کارمزد و با چک صیادی ارائه می‌شود.\n"
            "{خوشحال می‌شوم هر ابهام یا سوالی درباره طرح درمان دارید بفرمایید تا کامل راهنمایی کنم.|"
            "برای برآورد دقیق هزینه‌ها متناسب با شرایط دندانتان در خدمت شما هستم.}\n"
            "{در صورت تمایل می‌توانید شماره تماستان را ارسال بفرمایید تا برای ۱۰ دقیقه مشاوره تخصصی رایگان هماهنگ شود.|"
            "اگر مایل بودید شماره تماستون رو بگذارید تا برای بررسی دقیق‌تر خدمتتون تماس بگیریم.}"
        )
    elif is_procedure_inquiry:
        text = (
            "{درود بر شما|سلام وقتتون بخیر|سلام و احترام|درود}{ عزیز|}!\n"
            "بنده {consultant_name} هستم، مشاور تخصصی {business_name}.\n"
            "{پیرو سوال شما زیر پست درباره نحوه و مراحل انجام {topic}|در خصوص سوالتون پیرامون چگونگی این درمان}: "
            "این خدمات در کمترین تعداد جلسات با متریال معتبر اورجینال و بدون درد با متدهای دیجیتال انجام می‌گیرد و ویزیت اولیه نیز کاملاً رایگان است.\n"
            "{خوشحال می‌شوم هر سوالی درباره ماندگاری یا انتخاب روش مناسب دارید پاسخگو باشم.|"
            "پاسخگوی تمامی پرسش‌های تخصصی شما در این زمینه هستم.}\n"
            "{اگر مایل بودید شماره تماستان را ارسال کنید تا برای مشاوره دقیق تلفنی با شما هماهنگ شود.|"
            "می‌توانید همینجا بفرمایید یا شماره تماس بگذارید تا کامل راهنماییتان کنیم.}"
        )
    elif is_price_inquiry:
        text = (
            "{درود بر شما|سلام وقتتون بخیر|سلام و احترام|درود}{ عزیز|}!\n"
            "بنده {consultant_name} هستم، مشاور و پشتیبانی تخصصی {business_name}.\n"
            "{پیرو پیام شما زیر پست {topic}|در پاسخ به استعلام شما درباره {topic}}: "
            "تعرفه‌ها، پکیج‌های جشنواره و شرایط ویژه برای شما آماده شده است.\n"
            "{با توجه به اینکه وضعیت دندان‌های هر فرد متفاوت است، خوشحال می‌شوم راهنماییتان کنم.|"
            "برای انتخاب بهترین روش و برآورد متناسب با دندان‌های شما در خدمتم.}\n"
            "{در صورت تمایل می‌توانید شماره تماستان را بگذارید تا برای ۱۰ دقیقه مشاوره اختصاصی رایگان خدمتتان تماس بگیریم.|"
            "اگر مایل بودید شماره تماس ارسال بفرمایید تا برای هماهنگی با شما تماس حاصل شود.}"
        )
    else:
        text = (
            "{درود بر شما|سلام وقتتون بخیر|سلام و احترام|درود}{ عزیز|}!\n"
            "بنده {consultant_name} هستم، مشاور و پشتیبانی تخصصی {business_name}.\n"
            "{متوجه شدم|دیدم} پیگیر {topic} بودید.\n"
            "{خوشحال می‌شوم هر سوال یا ابهامی درباره هزینه‌ها یا نحوه انجام دارید راهنماییتان کنم.|"
            "پاسخگوی تمام سوالات تخصصی شما در این حوزه هستم.}\n"
            "{اگر مایل بودید شماره تماستان را ارسال بفرمایید تا برای یک مشاوره اختصاصی تلفنی خدمتتان تماس بگیریم.|"
            "می‌توانید همینجا بفرمایید یا شماره تماس بگذارید تا راهنمایی کامل دریافت کنید.}"
        )

    text = text.replace("{consultant_name}", consultant_name)
    text = text.replace("{business_name}", business_name)
    text = text.replace("{topic}", topic)
    text = text.replace("{clean_name}", clean_name)

    # Resolve Spintax
    text = parse_spintax(text)

    # Anti-Fingerprint Watermarking: textual polite closer (STRICTLY NO EMOJIS)
    closers = [
        "",
        "\nبا احترام",
        "\nبا آرزوی تندرستی",
        "\nارادتمند شما",
        "\nموفق و سلامت باشید",
        "\nدر پناه حق",
    ]
    selected_closer = random.choice(closers)
    if selected_closer and not any(text.endswith(c.strip()) for c in ["با احترام", "ارادتمند شما", "سلامت باشید", "در پناه حق"]):
        text = text.rstrip() + selected_closer

    return text


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
        from apps.core.prompts import get_system_prompt
        active_system = get_system_prompt("ig_outreach_pitches", system_prompt)
        res = chat_json(
            system=active_system,
            user=user_prompt,
            purpose="instagram_outreach_pitch",
            max_tokens=1500,
            timeout=25.0,
        )
        if isinstance(res, dict) and "pitches" in res and isinstance(res["pitches"], list) and len(res["pitches"]) > 0:
            return res["pitches"]
    except Exception as exc:
        logger.warning(f"LLM pitch generation failed, using tailored smart templates: {exc}", exc_info=True)

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
