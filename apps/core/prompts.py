"""Central Dynamic Prompt Management for Luna (gpt-6-luna) across all modules.

Allows viewing, customizing, and resetting system prompts dynamically via Telegram Bot and Django Admin,
with immediate in-memory and shared persistent file/DB propagation across all running workers.
"""
from __future__ import annotations

import importlib
import json
import os
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("core.prompts")

# Candidate paths for shared prompt overrides: PROMPTS_OVERRIDE_FILE, the container volume, the repo's data/
OVERRIDE_FILE_CANDIDATES = [
    *([Path(os.environ["PROMPTS_OVERRIDE_FILE"])] if os.environ.get("PROMPTS_OVERRIDE_FILE") else []),
    Path("/app/data/prompts_override.json"),
    Path(__file__).resolve().parent.parent.parent / "data" / "prompts_override.json",
]


def _get_override_file() -> Path:
    for candidate in OVERRIDE_FILE_CANDIDATES:
        try:
            if candidate.exists() or candidate.parent.exists():
                return candidate
        except Exception:
            continue
    fallback = Path(__file__).resolve().parent.parent.parent / "data" / "prompts_override.json"
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return fallback


# Thread-safe in-memory cache for fast lookups
_CACHE_LOCK = threading.Lock()
_CACHE_MTIME: float = 0.0
_PROMPTS_CACHE: Dict[str, str] = {}
_LAST_CACHE_CHECK: float = 0.0
CACHE_TTL = 3.0  # Check file/DB every 3 seconds for instant propagation


# ---------------------------------------------------------------------------
# Default Prompts Definition Catalog
# ---------------------------------------------------------------------------

PROMPT_CATALOG: Dict[str, Dict[str, Any]] = {
    # ── بخش اول: پایش و تحلیل توییتر (X) ──
    "need_system_x": {
        "title": "استخراج نیاز و لید از توییتر (X)",
        "category": "تحلیل شبکه X و توییتر",
        "module_path": "need_engine/prompts.py:NEED_SYSTEM_X",
        "description": "ارزیابی توییت‌های عمومی فارسی طبق ساختار ۷ سطحی قصد خرید، تشخیص خریدار واقعی، نیت خرید، مشورت، مقایسه یا بیان درد و فیلتر محتواهای نامرتبط، خبری و تبلیغاتی.",
        "default": None,  # text lives in module_path (single source of truth)
    },
    "need_system_x_product": {
        "title": "استخراج نیاز و لید محصول‌محور از توییتر (X)",
        "category": "تحلیل شبکه X و توییتر",
        "module_path": "need_engine/prompts.py:NEED_SYSTEM_X_PRODUCT",
        "description": "ارزیابی توییت‌های پیدا شده برای محصول یک فروشنده (حالت NE_X_MODE=product) با سلسله‌مراتب ۷ سطحی کیفیت لید.",
        "default": None,  # text lives in module_path (single source of truth)
    },
    "luna_qualification": {
        "title": "ارزیابی ۵ بعدی کیفیت و سیگنال خرید لونا",
        "category": "تحلیل شبکه X و توییتر",
        "module_path": "need_engine/post_qualification.py:LUNA_QUALIFICATION_SYSTEM",
        "description": "موتور ارزیابی ۵ بعدی پست‌ها بر اساس نوع محتوا (واقعی، طنز، خبری، تبلیغاتی)، سیگنال نیاز، مرحله آمادگی خرید، تطابق با کالای فروشنده و ارزش تجاری.",
        "default": None,  # text lives in module_path (single source of truth)
    },
    "x_query_terms": {
        "title": "تولید کوئری‌های ۵ گانه خریدارمحور برای توییتر",
        "category": "کالکتور و جستجوی توییتر",
        "module_path": "workers/x_collector/llm_terms.py:X_QUERY_TERMS_SYSTEM",
        "description": "تولید عبارت‌های جستجوی ۲ تا ۶ کلمه‌ای محاوره‌ای بدون ذکر نام فیزیکی کالا، در ۵ دسته قصد مستقیم، مشورت، بیان مشکل و درد، استعلام قیمت، و مقایسه.",
        "default": None,  # text lives in module_path (single source of truth)
    },
    "proqe_expansion_x": {
        "title": "توسعه تدریجی کوئری‌های توییتر (ProQE)",
        "category": "کالکتور و جستجوی توییتر",
        "module_path": "workers/x_collector/proqe.py:PROQE_EXPANSION_SYSTEM",
        "description": "تولید کوئری‌های جدید و بهینه ۱ تا ۳ کلمه‌ای بر اساس توییت‌های تاییدشده خریداران واقعی (نمونه‌های مثبت) و توییت‌های ردشده (نمونه‌های منفی).",
        "default": None,  # text lives in module_path (single source of truth)
    },
    "x_three_domains": {
        "title": "تولید کوئری‌های ۳ حوزه معنایی دسته‌های پایانی X",
        "category": "کالکتور و جستجوی توییتر",
        "module_path": "apps/discovery/leaf_query_generator.py:X_THREE_DOMAINS_SYSTEM",
        "description": "ساخت حداکثر ۱۰ عبارت جستجوی فارسی برای دسته‌بندی‌های پایانی در ۳ حوزه: علائم و دردها، قصد خرید و مشاوره، و سبک زندگی و موقعیت مصرف.",
        "default": None,  # text lives in module_path (single source of truth)
    },

    # ── بخش دوم: پایش و اسکرپ اینستاگرام ──
    "ig_five_queries": {
        "title": "تولید کوئری‌های مستقیم و کوتاه اینستاگرام",
        "category": "اسکرپر و کاوش اینستاگرام",
        "module_path": "apps/discovery/leaf_query_generator.py:INSTAGRAM_FIVE_QUERIES_SYSTEM",
        "description": "تولید ۵ عبارت کوتاه ۱ تا ۳ کلمه‌ای متمرکز بر عنوان دقیق محصول، مترادف‌های محاوره‌ای و خدمات جهت سرچ هشتگ و کلیدواژه در اینستاگرام.",
        "default": None,  # text lives in module_path (single source of truth)
    },
    "proqe_expand_ig": {
        "title": "توسعه و جایگزینی کوئری‌های اینستاگرام (ProQE)",
        "category": "اسکرپر و کاوش اینستاگرام",
        "module_path": "apps/discovery/instagram_families.py:expand_queries_for_plan_with_proqe",
        "description": "پیشنهاد کلیدواژه‌ها و هشتگ‌های جدید ۱ تا ۳ کلمه‌ای اینستاگرام بر اساس بازخورد کامنت‌های با تبدیل بالا و نمونه‌های موفق خرید.",
        "default": (
            "You are the ProQE (Progressive Query Expansion) engine for Persian buyer discovery on Instagram.\n"
            "You see confirmed buyer comments (real exemplars) and winning queries. Your task: propose 2-3 NEW general, high-intent Persian search terms.\n\n"
            "HARD RULES:\n"
            "1. Every phrase must be 1, 2 or 3 Persian words. Short and highly natural.\n"
            "2. Must be GENERAL consumer search words matching the product categories (e.g. «پیراهن مردانه», «ایمپلنت دندان», «لپ‌تاپ استوک», «مانتو تابستانه»).\n"
            "3. NEVER generate awkward, hyper-niche, or seller-irrelevant words like «اپ استور», «تولیدی شومیز», or single useless adjectives.\n"
            "4. Return strictly JSON:\n"
            "{\n"
            "  \"new_keywords\": [\"short phrase 1\", \"short phrase 2\"],\n"
            "  \"new_hashtags\": [\"tag1\", \"tag2\"]\n"
            "}"
        ),
    },
    "ig_comments_batch_eval": {
        "title": "ارزیابی دسته‌ای کامنت‌های اینستاگرام",
        "category": "اسکرپر و کاوش اینستاگرام",
        "module_path": "instagram_engine/analyzer.py:llm_evaluate_batch",
        "description": "ارزیابی دسته‌ای کامنت‌های فاقد کلمه کلیدی صریح برای شناسایی کامنت‌های حاوی نیاز واقعی، سوال جدی یا ابراز مشکل مرتبط با محصول.",
        "default": (
            "شما متخصص ارزیابی قصد خرید و لید در اینستاگرام هستید. با توجه به کپشن پست زیر، کامنت‌هایی که "
            "نشان‌دهنده نیاز واقعی به محصول، سوال جدی، ابراز درد/مشکل مرتبط با محصول، یا تمایل به خرید هستند را "
            "شناسایی کن. کامنت‌های تشکر ساده، ایموجی خالی، یا تعریف غیرخریدارانه را رد کن.\n\n"
            "پاسخ را دقیقاً در قالب یک آرایه JSON از شناسه‌ها برگردان. مثال: [\"123\", \"456\"]"
        ),
    },
    "ig_caption_reply_template": {
        "title": "الگوی پیام دایرکت به کامنت‌های استعلام اینستاگرام",
        "category": "ارتباط با مشتری و دایرکت",
        "module_path": "apps/discovery/management/commands/run_instagram_scraper.py:_get_caption_reply_template",
        "description": "نگارش خودکار متن پیش‌نویس پاسخ دایرکت محترمانه و ترغیب‌کننده متناسب با کپشن پست و مشخصات فروشگاه برای پاسخ به کامنت استعلام قیمت بدون ایموجی.",
        "default": (
            "شما متخصص فروش حرفه‌ای در دایرکت اینستاگرام هستید. "
            "با توجه به کپشن پست زیر و مشخصات فروشگاه، یک پیام دایرکت کوتاه، صمیمی، محترمانه و ترغیب‌کننده "
            "برای پاسخ به کامنت استعلام قیمت/خرید بنویسید.\n"
            "الزامات مهم:\n"
            "۱. حتماً در ابتدای پیام از برچسب {username} برای نام کاربر استفاده کنید (مثال: سلام {username} عزیز، وقتتون بخیر).\n"
            "۲. متن باید متناسب با کالای معرفی‌شده در کپشن باشد و حس ارسال پیام اختصاصی به کاربر را بدهد.\n"
            "۳. مطلقاً از هیچ‌گونه ایموجی، شکلک یا نمادی استفاده نکنید.\n"
            "۴. لینک مستقیم، آیدی کانال تلگرام یا شماره تماس قرار ندهید.\n"
            "۵. خروجی را در قالب JSON برگردانید: {\"template\": \"متن پیام...\"}"
        ),
    },
    "ig_caption_offer_discovery": {
        "title": "تحلیل کپشن پست برای کامنت‌های تک‌کلمه‌ای و عددی",
        "category": "تحلیل اینستاگرام و لیدها",
        "module_path": "instagram_engine/analyzer.py:analyze_post_caption_offer",
        "description": "بررسی کپشن پست اینستاگرام برای کامنت‌های تک کلمه یا عدد، کشف دقیق نیاز مشتری و تدوین پیام ساجست‌شده اختصاصی بدون ایموجی.",
        "default": (
            "شما تحلیل‌گر فروش و نیازسنجی اینستاگرام هستید. "
            "مخاطب در کامنت صرفاً یک عدد، کلمه کوتاه یا تاییدیه ارسال کرده است. "
            "وظیفه شما این است که منحصراً بر مبنای کپشن پست، تشخیص دهید چه کالا، خدمت یا جشنواره‌ای در این پست ارائه شده است؛ "
            "سپس نیاز اصلی متقاضی را در یک عبارت کوتاه مشخص کرده و یک پیام دایرکت اختصاصی، مودبانه، صمیمی و کاملاً بدون ایموجی برای او تدوین کنید.\n"
            "الزامات قطعی و بدون تغییر:\n"
            "۱. در ابتدای پیام دایرکت از {username} برای نام مخاطب استفاده کنید (مانند: سلام {username} عزیز، وقتتون بخیر).\n"
            "۲. از هیچ‌گونه ایموجی، شکلک یا نمادی استفاده نکنید.\n"
            "۳. از درج لینک مستقیم، شماره تماس یا آیدی کانال خودداری فرمایید.\n"
            "۴. خروجی را دقیقاً در قالب ساختار JSON با این ۳ کلید برگردانید:\n"
            "{\"need\": \"شرح نیاز متقاضی\", \"suggested_reply\": \"متن کامل پیام پیشنهادی دایرکت\", \"offer\": \"عنوان خدمت یا کالا\"}"
        ),
    },

    # ── بخش سوم: ارتباط با مشتری و پیش‌نویس پاسخ‌ها (Outreach) ──
    "ig_outreach_pitches": {
        "title": "پیشنهاد ۳ پیش‌نویس متنوع پیام دایرکت در پنل",
        "category": "ارتباط با مشتری و دایرکت",
        "module_path": "apps/discovery/outreach_service.py:generate_ai_outreach_pitches",
        "description": "ساخت ۳ مدل پیام دایرکت متناسب با لحن مشاور (مشاوره‌ای و صمیمی، دعوت به مشاوره رایگان، بررسی تخصصی نیاز) برای ارسال به مشتری.",
        "default": (
            "شما یک متخصص بازاریابی دیجیتال و کپی‌رایتر ارشد برای دایرکت مارکتینگ اینستاگرام هستید. "
            "وظیفه شما نوشتن ۳ پیش‌نویس پیام دایرکت (Cold DM) کاملاً طبیعی، صمیمی و بدون شباهت به ربات است.\n"
            "قوانین حیاتی ضد اسپم اینستاگرام:\n"
            "۱. پیام باید کوتاه (حداکثر ۳ یا ۴ جمله)، طبیعی و محترمانه باشد.\n"
            "۲. در پیام اول به هیچ عنوان لینک وب‌سایت یا آیدی کانال قرار ندهید (باعث بلاک می‌شود).\n"
            "۳. هدف پیام باز کردن گفتگو (Open a conversation) است، نه فروش تهاجمی.\n"
            "۴. لحن باید متناسب با هویت مشاور و کسب‌وکار باشد.\n"
            "خروجی باید یک آبجکت JSON معتبر به فرمت زیر باشد:\n"
            "{\n"
            "  \"pitches\": [\n"
            "    {\"id\": 1, \"title\": \"مشاوره‌ای و صمیمی\", \"text\": \"...\", \"badge\": \"پیشنهادی\"},\n"
            "    {\"id\": 2, \"title\": \"دعوت به مشاوره رایگان ۱۰ دقیقه‌ای\", \"text\": \"...\", \"badge\": \"مکالمه‌محور\"},\n"
            "    {\"id\": 3, \"title\": \"طرح سوال و بررسی تخصصی نیاز\", \"text\": \"...\", \"badge\": \"ارزیابی تخصصی\"}\n"
            "  ]\n"
            "}"
        ),
    },
    "outreach_system_reply": {
        "title": "نگارش پیش‌نویس پاسخ به فرصت‌ها و لیدها",
        "category": "ارتباط با مشتری و دایرکت",
        "module_path": "need_engine/reply.py:OUTREACH_SYSTEM",
        "description": "تولید ۳ نسخه پاسخ با لحن‌های کوتاه، دوستانه و مشاوره‌ای به پیام خریدار در شبکه ایکس یا اینستاگرام همراه با استناد به نیاز مطرح‌شده.",
        "default": None,  # text lives in module_path (single source of truth)
    },

    # ── بخش چهارم: پایش پیام‌های تلگرام ──
    "need_system_tg": {
        "title": "استخراج فرصت‌های خرید از گروه‌های تلگرام",
        "category": "پایش پیام‌های تلگرام",
        "module_path": "need_engine/prompts.py:NEED_SYSTEM",
        "description": "بررسی مکالمات گروه‌های عمومی تلگرام و شناسایی افراد دارای نیاز واقعی، درد یا پرسش مرتبط با خرید محصول یا خدمات.",
        "default": None,  # text lives in module_path (single source of truth)
    },

    # ── بخش پنجم: دسته‌بندی و مدیریت کالاها ──
    "taxonomy_tree": {
        "title": "طراحی درخت دسته‌بندی کالا و خدمات",
        "category": "دسته‌بندی و مدیریت کالاها",
        "module_path": "apps/products/taxonomy.py:TREE_SYSTEM",
        "description": "طراحی ساختار شاخه‌ها و زیردسته‌های سطح ۲ و ۳ متناسب با فرهنگ بازار و اصطلاحات متداول فروشندگان و خریداران ایرانی.",
        "default": None,  # text lives in module_path (single source of truth)
    },
    "taxonomy_organize": {
        "title": "سازماندهی خودکار کالاها در درخت دسته‌بندی",
        "category": "دسته‌بندی و مدیریت کالاها",
        "module_path": "apps/products/taxonomy.py:ORGANIZE_SYSTEM",
        "description": "بررسی محصولات جدید بدون دسته‌بندی فروشنده و تخصیص عمیق‌ترین و مناسب‌ترین گره در درخت دسته‌بندی یا پیشنهاد گره جدید.",
        "default": None,  # text lives in module_path (single source of truth)
    },
    "suggest_category": {
        "title": "پیشنهاد هوشمند عمیق‌ترین دسته‌بندی کالا",
        "category": "دسته‌بندی و مدیریت کالاها",
        "module_path": "apps/products/services.py:suggest_category",
        "description": "انتخاب بهترین شناسه دسته‌بندی کالا بر اساس عنوان و توضیحات محصول با اولویت شاخه‌های پایانی (سطح ۳).",
        "default": (
            "You are an expert Iranian marketplace taxonomy classifier.\n"
            "Given a product title and description, select the SINGLE BEST category ID from the candidate categories.\n"
            "CRITICAL: Always select the deepest, most specific leaf category (level 3 or deepest level).\n"
            "Never select a parent category when a specific child fits.\n"
            "Return strictly JSON: {\"category_id\": <number>, \"reason\": \"<Persian explanation>\"}"
        ),
    },
    "verify_system": {
        "title": "ارزیابی تطابق محصول با نیاز استخراج‌شده",
        "category": "تحلیل نیاز و فرصت‌ها",
        "module_path": "need_engine/prompts.py:VERIFY_SYSTEM",
        "description": "بررسی اینکه آیا محصولات پیشنهادی فروشنده نیاز اصلی مشتری مطرح‌شده در پیام را برطرف می‌نمایند یا خیر.",
        "default": None,  # text lives in module_path (single source of truth)
    },
    "product_card_system": {
        "title": "تبدیل مشخصات کالا به کارت فشرده جستجو",
        "category": "دسته‌بندی و مدیریت کالاها",
        "module_path": "need_engine/prompts.py:PRODUCT_CARD_SYSTEM",
        "description": "تبدیل آگهی و مشخصات کالا به کارت خلاصه شامل چیستی، مترادف‌ها، مشکلات قابل حل و گروه مخاطب.",
        "default": None,  # text lives in module_path (single source of truth)
    },
}


# ---------------------------------------------------------------------------
# Storage & Retrieval Engine
# ---------------------------------------------------------------------------

def _load_overrides_from_file() -> Dict[str, str]:
    f = _get_override_file()
    if f.exists():
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                if isinstance(data, dict):
                    return {k: str(v) for k, v in data.items() if v}
        except Exception as e:
            log.warning("Could not read prompt overrides file %s: %s", f, e)
    return {}


def _save_overrides_to_file(overrides: Dict[str, str]) -> None:
    for candidate in OVERRIDE_FILE_CANDIDATES:
        try:
            candidate.parent.mkdir(parents=True, exist_ok=True)
            with open(candidate, "w", encoding="utf-8") as fp:
                json.dump(overrides, fp, ensure_ascii=False, indent=2)
        except Exception:
            continue


def _refresh_cache_if_needed() -> None:
    global _LAST_CACHE_CHECK, _CACHE_MTIME, _PROMPTS_CACHE
    now = time.time()
    if now - _LAST_CACHE_CHECK < CACHE_TTL:
        return

    _LAST_CACHE_CHECK = now
    f = _get_override_file()
    mtime = f.stat().st_mtime if f.exists() else 0.0

    if mtime != _CACHE_MTIME or not _PROMPTS_CACHE:
        with _CACHE_LOCK:
            _CACHE_MTIME = mtime
            overrides = _load_overrides_from_file()

            # Merge with database if available
            try:
                from django.db import connection
                from apps.core.models import LLMPromptConfig

                # The engine calls this from its ThreadPoolExecutor workers, which never get Django's end-of-request
                # cleanup: a connection opened here would stay open (and block dropping the test database).
                opened_here = connection.connection is None
                try:
                    for row in LLMPromptConfig.objects.filter(is_customized=True):
                        if row.prompt_text and row.prompt_text.strip():
                            overrides[row.key] = row.prompt_text.strip()
                finally:
                    if opened_here and not connection.in_atomic_block:
                        connection.close()
            except Exception:
                pass

            _PROMPTS_CACHE = overrides


_DEFAULT_TEXT_CACHE: Dict[str, str] = {}


def default_prompt(key: str) -> str:
    """Factory default for a catalog key.

    Prompts whose ``module_path`` points at a module-level string constant are
    read from that constant, so the text a developer edits in code is the one
    that runs (and the one the admin panel shows / resets to). Other entries
    keep their text in the catalog.
    """
    meta = PROMPT_CATALOG.get(key)
    if not meta:
        return ""
    if key in _DEFAULT_TEXT_CACHE:
        return _DEFAULT_TEXT_CACHE[key]
    text = meta.get("default") or ""
    module_path, _, attr = (meta.get("module_path") or "").partition(":")
    if not text and module_path.endswith(".py") and attr:
        try:
            value = getattr(importlib.import_module(module_path[:-3].replace("/", ".")), attr, None)
        except Exception:
            log.warning("Could not load default prompt %s from %s", key, meta["module_path"], exc_info=True)
            value = None
        if isinstance(value, str):
            text = value.strip()
    _DEFAULT_TEXT_CACHE[key] = text
    return text


def get_system_prompt(key: str, default: Optional[str] = None) -> str:
    """
    Returns the active system prompt for the given key.

    Order: admin override (shared file / DB) -> the ``default`` the caller
    passes (its module constant) -> the catalog default.
    """
    _refresh_cache_if_needed()

    with _CACHE_LOCK:
        if key in _PROMPTS_CACHE and _PROMPTS_CACHE[key].strip():
            return _PROMPTS_CACHE[key].strip()

    if default and default.strip():
        return default.strip()

    return default_prompt(key)


def set_system_prompt(key: str, new_text: str) -> Tuple[bool, str]:
    """
    Updates the system prompt for a key in DB, shared file, and in-memory cache.
    Returns (success: bool, message: str).
    """
    if key not in PROMPT_CATALOG:
        return False, f"شناسه پرامپت «{key}» در کاتالوگ سامانه تعریف نشده است."

    cleaned = (new_text or "").strip()
    if not cleaned or len(cleaned) < 10:
        return False, "متن پرامپت نمی‌تواند خالی یا کمتر از ۱۰ کاراکتر باشد."

    meta = PROMPT_CATALOG[key]

    # 1. Update shared file
    overrides = _load_overrides_from_file()
    overrides[key] = cleaned
    _save_overrides_to_file(overrides)

    # 2. Update memory cache
    with _CACHE_LOCK:
        _PROMPTS_CACHE[key] = cleaned
        _CACHE_MTIME = time.time()

    # 3. Update or create DB record if Django ORM available
    db_updated = False
    try:
        from apps.core.models import LLMPromptConfig
        obj, created = LLMPromptConfig.objects.get_or_create(
            key=key,
            defaults={
                "title": meta["title"],
                "category": meta["category"],
                "module_path": meta.get("module_path", ""),
                "description": meta["description"],
                "default_prompt": default_prompt(key),
                "prompt_text": cleaned,
                "is_customized": True,
                "version": 1,
            }
        )
        if not created:
            obj.title = meta["title"]
            obj.category = meta["category"]
            obj.description = meta["description"]
            obj.prompt_text = cleaned
            obj.is_customized = True
            obj.version += 1
            obj.save()
        db_updated = True
    except Exception as e:
        log.warning("DB prompt update notice: %s", e)

    log.info("Prompt '%s' updated (length: %d chars, DB: %s)", key, len(cleaned), db_updated)
    return True, f"پرامپت «{meta['title']}» با موفقیت به‌روزرسانی شد و بلافاصله در چرخه پردازش‌های بعدی اعمال گردید."


def reset_system_prompt(key: str) -> Tuple[bool, str]:
    """
    Resets a prompt to its factory default.
    """
    if key not in PROMPT_CATALOG:
        return False, f"شناسه پرامپت «{key}» یافت نشد."

    meta = PROMPT_CATALOG[key]
    default_text = default_prompt(key)

    # 1. Remove from shared file
    overrides = _load_overrides_from_file()
    if key in overrides:
        del overrides[key]
        _save_overrides_to_file(overrides)

    # 2. Remove from memory cache
    with _CACHE_LOCK:
        if key in _PROMPTS_CACHE:
            del _PROMPTS_CACHE[key]
        _CACHE_MTIME = time.time()

    # 3. Update DB record
    try:
        from apps.core.models import LLMPromptConfig
        obj = LLMPromptConfig.objects.filter(key=key).first()
        if obj:
            obj.prompt_text = default_text
            obj.is_customized = False
            obj.version += 1
            obj.save(update_fields=["prompt_text", "is_customized", "version", "updated_at"])
    except Exception as e:
        log.warning("DB prompt reset notice: %s", e)

    return True, f"پرامپت «{meta['title']}» با موفقیت به حالت پیش‌فرض اولیه بازگردانی شد."


def get_prompt_info(key: str) -> Optional[Dict[str, Any]]:
    """Returns detailed metadata and current text for a prompt."""
    if key not in PROMPT_CATALOG:
        return None

    meta = dict(PROMPT_CATALOG[key])
    meta["key"] = key
    meta["default"] = default_prompt(key)
    current_prompt = get_system_prompt(key)
    is_custom = False

    try:
        from apps.core.models import LLMPromptConfig
        row = LLMPromptConfig.objects.filter(key=key).first()
        if row:
            is_custom = row.is_customized
            meta["version"] = row.version
            meta["updated_at"] = row.updated_at
    except Exception:
        pass

    if not is_custom:
        overrides = _load_overrides_from_file()
        is_custom = bool(overrides.get(key) and overrides[key] != meta["default"])

    meta["current_prompt"] = current_prompt
    meta["is_customized"] = is_custom
    return meta


def get_all_prompts_info() -> List[Dict[str, Any]]:
    """Returns metadata list of all catalog system prompts."""
    results = []
    for key in PROMPT_CATALOG:
        info = get_prompt_info(key)
        if info:
            results.append(info)
    return results


def ensure_prompts_seeded() -> None:
    """Populates LLMPromptConfig in DB with any missing catalog prompts."""
    try:
        from apps.core.models import LLMPromptConfig
        for key, meta in PROMPT_CATALOG.items():
            obj, created = LLMPromptConfig.objects.get_or_create(
                key=key,
                defaults={
                    "title": meta["title"],
                    "category": meta["category"],
                    "module_path": meta.get("module_path", ""),
                    "description": meta["description"],
                    "prompt_text": default_prompt(key),
                    "default_prompt": default_prompt(key),
                    "is_customized": False,
                    "version": 1,
                }
            )
            if not created and obj.default_prompt != default_prompt(key):
                # keep the stored factory default in sync with the code
                obj.default_prompt = default_prompt(key)
                fields = ["default_prompt"]
                if not obj.is_customized:
                    obj.prompt_text = obj.default_prompt
                    fields.append("prompt_text")
                obj.save(update_fields=fields)
    except Exception as e:
        log.debug("Seeding prompts notice: %s", e)
