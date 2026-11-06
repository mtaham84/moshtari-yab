from __future__ import annotations

import html
import os
import re
import sys
import traceback
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

TEHRAN_TZ = ZoneInfo("Asia/Tehran")

# Masking patterns for sensitive parameters in error messages and stack traces
SENSITIVE_PATTERNS = [
    re.compile(r'(?i)(password|secret|token|api[_-]?key|bearer\s+[a-zA-Z0-9_\-\.]+)([:=]\s*["\']?)([^"\'\s,;]+)(["\']?)'),
    re.compile(r'(?i)(postgres(?:ql)?://|socks5h?://|https?://)([^:@\s]+):([^\s]+)@([a-zA-Z0-9_\-\.]+)'),
]

# Conceptual diagnosis for common exception types
ROOT_CAUSE_ANALYSIS_MAP: dict[str, str] = {
    "ConnectionError": "عدم دسترسی به سرور مقصد یا قطع ارتباط پروکسی / شبکه.",
    "ConnectTimeout": "پایان مهلت برقراری ارتباط اولیه با سرور مقصد یا پروکسی (Connection Timeout).",
    "ReadTimeout": "سرور مقصد در مهلت تعیین‌شده پاسخی ارسال نکرد (Read Timeout).",
    "TimeoutError": "تایم‌اوت در عملیات شبکه یا سوکت سیستم.",
    "OperationalError": "خطا در اتصال به پایگاه داده PostgreSQL یا قطع شدن تراکنش جاری.",
    "IntegrityError": "نقض قید یکتا یا کلید خارجی در پایگاه داده (داده تکراری یا نامعتبر).",
    "DatabaseError": "خطای عمومی در ارتباط یا اجرای فرامین پایگاه داده.",
    "RedisError": "عدم دسترسی یا خطای اتصال به سرور حافظه واسط Redis.",
    "ConnectionRefusedError": "سرویس یا پورت مقصد فعال نبوده و ارتباط را پس زده است.",
    "KeyError": "کلید مورد نظر در ساختار دیکشنری یا داده‌های دریافتی یافت نشد.",
    "AttributeError": "فراخوانی صفت یا متدی که در این شیء تعریف نشده است.",
    "TypeError": "عدم تطابق نوع ورودی در فراخوانی توابع یا عملیات محاسباتی.",
    "ValueError": "فرمت یا مقدار داده ورودی نامعتبر یا غیرمنتظره است.",
    "FileNotFoundError": "فایل یا مسیر سیستمی مورد نظر در دیسک پیدا نشد.",
    "PermissionError": "عدم وجود مجوز کافی جهت دسترسی به فایل یا منبع سیستم.",
    "JSONDecodeError": "پاسخ دریافتی ساختار معتبر JSON ندارد (احتمال صفحه خطای HTML سرور مقابل).",
    "FloodWait": "محدودسازی موقت توسط سرور تلگرام به دلیل تعدد درخواست‌ها (Rate Limit).",
}


def sanitize_text(text: str) -> str:
    """Masks credentials, passwords, and tokens from text."""
    if not text:
        return ""
    result = text
    # Mask password/token fields
    result = SENSITIVE_PATTERNS[0].sub(r"\1\2***MASKED***\4", result)
    # Mask proxy / DB connection URLs with credentials
    result = SENSITIVE_PATTERNS[1].sub(r"\1\2:***@\4", result)
    return result


def get_tehran_now() -> tuple[str, str]:
    """Returns (gregorian_str, jalali_str) for the current moment in Asia/Tehran."""
    now_tehran = datetime.now(TEHRAN_TZ)
    gregorian_str = now_tehran.strftime("%Y-%m-%d %H:%M:%S")

    jalali_str = ""
    try:
        from apps.core.jalali import format_jalali_datetime
        jalali_str = format_jalali_datetime(now_tehran, persian_digits=True)
    except Exception:
        # Fallback if Django / apps.core is not yet in path
        jalali_str = gregorian_str

    return gregorian_str, jalali_str


def detect_service_context() -> str:
    """Detects which component is running (Django web, Celery, Crawler, Outreach, etc.)."""
    cmd = " ".join(sys.argv)
    if "gunicorn" in cmd or "runserver" in cmd or "uvicorn" in cmd:
        return "Web (Django HTTP)"
    if "celery" in cmd:
        return "Celery Worker"
    if "run_instagram_outreach" in cmd:
        return "Instagram Outreach Loop"
    if "run_instagram_scraper" in cmd:
        return "Instagram Scraper"
    if "telegram_crawler" in cmd:
        return "Telegram Crawler"
    if "need_engine" in cmd:
        return "Need Engine"
    if "run_channel_config_automation" in cmd:
        return "Channel Config Automation"
    if "x_collector" in cmd or "sync_x_status" in cmd:
        return "X (Twitter) Collector"
    return "App Background Daemon"


def get_clean_path(raw_path: str) -> str:
    """Converts absolute or docker file paths to clean repo-relative paths."""
    if not raw_path:
        return "نامشخص"
    for prefix in ("/app/", "/home/javad/Projects/moshtari-yab/", "/home/javad/Projects/moshtari-yab-alerts/"):
        if raw_path.startswith(prefix):
            return raw_path[len(prefix):]
    # If it contains project directory keywords
    for keyword in ("apps/", "need_engine/", "telegram_crawler/", "workers/", "shared/", "xray/", "config/"):
        idx = raw_path.find(keyword)
        if idx != -1:
            return raw_path[idx:]
    return os.path.basename(raw_path)


def extract_relevant_stack_frames(tb: Any, max_frames: int = 3) -> list[str]:
    """Filters traceback to show only frames from project codebase, skipping framework noise."""
    if not tb:
        return []

    extracted: list[str] = []
    frames = traceback.extract_tb(tb)
    project_keywords = ("apps/", "need_engine/", "telegram_crawler/", "workers/", "shared/", "xray/", "config/")

    for frame in frames:
        clean_file = get_clean_path(frame.filename)
        # Check if frame belongs to project
        is_project = any(clean_file.startswith(kw) for kw in project_keywords)
        if is_project or not extracted:  # Keep project frames or at least one frame
            line_info = f"File \"{clean_file}\", line {frame.lineno}, in {frame.name}"
            if frame.line:
                line_info += f"\n  {frame.line.strip()}"
            extracted.append(line_info)

    # Return the last relevant frames
    return extracted[-max_frames:]


def format_error_alert(
    record_dict: dict[str, Any],
    fingerprint: str,
    repeat_count: int = 1,
) -> str:
    """Builds a human-readable, well-structured Telegram HTML message explaining WHERE and WHY."""
    gregorian_dt, jalali_dt = get_tehran_now()
    service = detect_service_context()

    level = record_dict.get("levelname", "ERROR")
    module = record_dict.get("module", "")
    func_name = record_dict.get("funcName", "")
    raw_path = record_dict.get("pathname", "")
    lineno = record_dict.get("lineno", 0)
    clean_file = get_clean_path(raw_path)

    exc_type = record_dict.get("exc_type") or "Exception"
    exc_msg = sanitize_text(record_dict.get("exc_msg") or record_dict.get("message") or "بدون توضیحات")
    raw_tb = record_dict.get("exc_tb")

    # Conceptual analysis
    diagnosis = ROOT_CAUSE_ANALYSIS_MAP.get(exc_type, "خطای داخلی در منطق اجرای برنامه.")

    # Location
    location_str = f"<code>{html.escape(clean_file)}:{lineno}</code>"
    if func_name and func_name != "<module>":
        location_str += f" (در تابع <code>{html.escape(func_name)}</code>)"

    # Stack frames
    frames = extract_relevant_stack_frames(raw_tb, max_frames=3)
    frames_block = ""
    if frames:
        cleaned_frames = "\n".join(sanitize_text(f) for f in frames)
        frames_block = f"\n\n<b>ردپای فراخوانی در پروژه:</b>\n<pre>{html.escape(cleaned_frames[:800])}</pre>"

    # HTTP request info (if attached to record)
    req_info = ""
    http_req = record_dict.get("request_info")
    if http_req:
        req_info = f"\n• <b>درخواست وب:</b> <code>{html.escape(http_req)}</code>"

    # Repetition notice
    repetition_badge = ""
    if repeat_count > 1:
        repetition_badge = f"\n<i>(این خطا {repeat_count} بار در بازه اخیر تکرار شده است)</i>"

    message = (
        "<b>[گزارش خطای سامانه]</b>\n"
        f"<b>سطح:</b> <code>{html.escape(level)}</code> | <b>سرویس:</b> <code>{html.escape(service)}</code>\n"
        f"<b>زمان (ایران):</b> {html.escape(jalali_dt)} [{html.escape(gregorian_dt)}]\n\n"
        f"<b>محل وقوع خطا (کجا؟):</b>\n"
        f"• <b>فایل و سطر:</b> {location_str}\n"
        f"• <b>ماژول:</b> <code>{html.escape(module)}</code>"
        f"{req_info}\n\n"
        f"<b>علت و ریشه خطا (چرا؟):</b>\n"
        f"• <b>نوع استثنا:</b> <code>{html.escape(exc_type)}</code>\n"
        f"• <b>تحلیل ریشه‌ای:</b> {html.escape(diagnosis)}\n"
        f"• <b>شرح خطا:</b>\n"
        f"<code>{html.escape(exc_msg[:400])}</code>"
        f"{frames_block}\n\n"
        f"<b>شناسه خطا:</b> <code>#{html.escape(fingerprint)}</code>"
        f"{repetition_badge}"
    )

    return message
