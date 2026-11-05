#!/usr/bin/env python3
"""
Telegram Config Management Bot (@config_peyda_bot)
- Listens for incoming proxy links (vless://, vmess://, trojan://, ss://)
- Validates config formats strictly
- Tests live latency (ping_ms) via Xray
- Adds healthy configs to database and assigns dedicated Xray ports per account
- Automatically deletes/prunes dead configs (ping == -1) from server & DB
- Reports live status and account-proxy assignments
- Connects through local proxy (socks5/http) since Telegram API is filtered
"""
from __future__ import annotations

import html
import logging
import os
import re
import sys
import threading
import time
import urllib.parse
from typing import Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("telegram_config_bot")

# Setup Django Environment & Load .env
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
for env_candidate in [REPO_ROOT / ".env"]:
    if env_candidate.exists():
        with open(env_candidate, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# The bot token lives only in .env (CONFIG_BOT_TOKEN); it is never stored in the repository.
BOT_TOKEN = os.environ.get("CONFIG_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    sys.exit("CONFIG_BOT_TOKEN is not set (add it to .env)")
TG_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

# When running on host, PostgreSQL is bound to localhost 127.0.0.1
if not Path("/.dockerenv").exists():
    if os.environ.get("POSTGRES_HOST") in (None, "db", "172.18.0.3"):
        os.environ["POSTGRES_HOST"] = "127.0.0.1"

try:
    import django
    django.setup()
except Exception as exc:
    log.warning("Django setup warning: %s", exc)

from shared.telegram_alerts import load_subscribers, save_subscribers  # noqa: E402
from xray.multi_proxy_manager import (
    validate_config_format,
    add_proxy_config,
    prune_dead_configs,
    assign_proxies_to_accounts,
    rebuild_xray_config_file,
    reload_xray_service
)


def get_proxy_candidates() -> list[str]:
    """Returns list of proxy candidates to try for Telegram API."""
    candidates = []
    if os.environ.get("TG_BOT_PROXY"):
        candidates.append(os.environ["TG_BOT_PROXY"].strip())
    if Path("/.dockerenv").exists():
        candidates.extend(["socks5h://xray:1080", "http://xray:8080"])
    else:
        candidates.extend(["socks5h://127.0.0.1:1080", "http://127.0.0.1:8080"])
    return candidates


def tg_request(method: str, payload: dict[str, Any] | None = None, timeout: int = 35) -> dict[str, Any] | None:
    """Sends a request to Telegram Bot API via requests library using local proxy."""
    import requests
    url = f"{TG_API_BASE}/{method}"
    candidates = get_proxy_candidates()

    last_exc = None
    for proxy_url in candidates:
        proxies = {"http": proxy_url, "https": proxy_url}
        try:
            if payload:
                resp = requests.post(url, json=payload, proxies=proxies, timeout=timeout)
            else:
                resp = requests.get(url, proxies=proxies, timeout=timeout)

            data = resp.json()
            if not data.get("ok"):
                log.warning("Telegram API error (%s): %s", method, data)
            return data
        except Exception as exc:
            last_exc = exc
            continue

    log.warning("Telegram request failed (%s) across all proxy candidates: %s", method, last_exc)
    return None


def send_message(chat_id: int | str, text: str, reply_markup: dict[str, Any] | None = None, parse_mode: str = "HTML") -> None:
    """Sends a formatted message to a Telegram chat, auto-chunking if exceeding 3800 chars."""
    if not text:
        return

    MAX_CHUNK = 3800
    if len(text) <= MAX_CHUNK:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        tg_request("sendMessage", payload, timeout=12)
        return

    lines = text.split("\n")
    chunks = []
    current_chunk = []
    current_len = 0

    for line in lines:
        if current_len + len(line) + 1 > MAX_CHUNK and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = len(line)
        else:
            current_chunk.append(line)
            current_len += len(line) + 1

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    for idx, chunk in enumerate(chunks):
        is_last = (idx == len(chunks) - 1)
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if is_last and reply_markup:
            payload["reply_markup"] = reply_markup
        tg_request("sendMessage", payload, timeout=12)
        if not is_last:
            time.sleep(0.15)


# In-memory user state tracker for interactive multi-step commands (e.g. prompt editing)
USER_STATES: dict[int | str, dict[str, Any]] = {}


def get_subscribers() -> set[int]:
    return load_subscribers()


def register_subscriber(chat_id: int | str) -> None:
    try:
        cid = int(chat_id)
        current = get_subscribers()
        if cid not in current:
            current.add(cid)
            save_subscribers(current)
    except Exception as e:
        log.warning("Could not register subscriber %s: %s", chat_id, e)


def broadcast_message(text: str, reply_markup: dict[str, Any] | None = None) -> None:
    for cid in get_subscribers():
        try:
            send_message(cid, text, reply_markup=reply_markup)
            time.sleep(0.05)
        except Exception as e:
            log.warning("Failed sending broadcast to %s: %s", cid, e)


def check_luna_api_status() -> dict[str, Any]:
    """
    Probes Luna API (gpt-6-luna on Houshyar24) to evaluate live service health and credit status.
    """
    import requests
    from apps.billing.models import Provider

    provider = Provider.objects.filter(name__icontains="houshyar", is_active=True).first()
    if not provider:
        provider = Provider.objects.filter(is_active=True).first()

    if not provider:
        return {
            "ok": False,
            "status_code": 0,
            "credit_exhausted": False,
            "message": "هیچ سرویس‌دهنده فعالی در پایگاه داده تنظیم نشده است.",
            "latency_ms": 0.0,
            "model": "gpt-6-luna",
            "provider": "نامشخص",
            "base_url": "",
        }

    model_name = "gpt-6-luna"
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
        "User-Agent": "MoshtariYab/1.0"
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
        "temperature": 0.0
    }

    t0 = time.time()
    try:
        url = f"{provider.base_url.rstrip('/')}/chat/completions"
        r = requests.post(url, json=payload, headers=headers, timeout=12, proxies={"http": None, "https": None})
        latency = round((time.time() - t0) * 1000, 1)

        if r.status_code == 200:
            return {
                "ok": True,
                "status_code": 200,
                "credit_exhausted": False,
                "message": "سرویس فعال، برخط و دارای اعتبار کافی است.",
                "latency_ms": latency,
                "model": model_name,
                "provider": provider.name,
                "base_url": provider.base_url,
            }
        elif r.status_code == 402:
            return {
                "ok": False,
                "status_code": 402,
                "credit_exhausted": True,
                "message": "اعتبار حساب لونا به اتمام رسیده است (کد ۴۰۲ - Insufficient Credits).",
                "latency_ms": latency,
                "model": model_name,
                "provider": provider.name,
                "base_url": provider.base_url,
            }
        else:
            err_msg = ""
            try:
                err_data = r.json()
                err_msg = err_data.get("error", {}).get("message") or r.text[:200]
            except Exception:
                err_msg = r.text[:200]
            is_insufficient = any(w in err_msg.lower() for w in ("insufficient", "credit", "quota", "اعتبار"))
            return {
                "ok": False,
                "status_code": r.status_code,
                "credit_exhausted": is_insufficient or r.status_code == 402,
                "message": f"خطای ارائه‌دهنده ({r.status_code}): {err_msg}",
                "latency_ms": latency,
                "model": model_name,
                "provider": provider.name,
                "base_url": provider.base_url,
            }
    except Exception as exc:
        latency = round((time.time() - t0) * 1000, 1)
        return {
            "ok": False,
            "status_code": 0,
            "credit_exhausted": False,
            "message": f"خطا در برقراری ارتباط با سرور: {exc}",
            "latency_ms": latency,
            "model": model_name,
            "provider": provider.name,
            "base_url": provider.base_url,
        }


def get_main_keyboard() -> dict[str, Any]:
    """Returns main menu reply keyboard in Persian."""
    return {
        "keyboard": [
            [{"text": "📊 وضعیت کانفیگ‌ها و سرور"}, {"text": "🧠 وضعیت API و اعتبار لونا"}],
            [{"text": "👥 اکانت‌ها و پروکسی‌های متصل"}, {"text": "📋 درخواست‌های فعال‌سازی"}],
            [{"text": "مدیریت پرامپت‌های لونا (LLM)"}, {"text": "لیدهای پرامتیاز اینستاگرام"}],
            [{"text": "🔄 تست مجدد و پاکسازی خرابی‌ها"}, {"text": "❓ راهنمای ارسال کانفیگ"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


def handle_activation_requests(chat_id: int | str) -> None:
    """Lists recent activation and demo requests."""
    register_subscriber(chat_id)
    from apps.accounts.models import ActivationRequest
    from apps.core.jalali import format_jalali_datetime

    requests_qs = list(ActivationRequest.objects.all().order_by("-created_at")[:10])
    if not requests_qs:
        send_message(
            chat_id,
            "📭 <b>هیچ درخواست فعال‌سازی ثبت‌شده‌ای یافت نشد.</b>\n"
            "• به محض ثبت درخواست در صفحه دمو، اعلان آن بلافاصله اینجا مخابره خواهد شد.",
            reply_markup=get_main_keyboard()
        )
        return

    msg_lines = [
        "📋 <b>فهرست درخواست‌های فعال‌سازی و دمو (۱۰ مورد اخیر):</b>\n"
    ]
    status_icons = {
        ActivationRequest.STATUS_PENDING: "⏳",
        ActivationRequest.STATUS_APPROVED: "🔹",
        ActivationRequest.STATUS_ACTIVATED: "✅",
        ActivationRequest.STATUS_REJECTED: "❌",
    }

    for i, req in enumerate(requests_qs, 1):
        icon = status_icons.get(req.status, "▫️")
        try:
            dt_str = format_jalali_datetime(req.created_at)
        except Exception:
            dt_str = str(req.created_at)[:16]

        esc_name = html.escape(req.full_name)
        esc_comp = html.escape(req.company_name)
        line = (
            f"{i}. {icon} <b>{esc_name}</b> ({esc_comp})\n"
            f"   📞 تماس: <code>{req.phone_number}</code> | وضعیت: <b>{req.get_status_display()}</b>\n"
            f"   📅 ثبت: {dt_str}\n"
            f"   🔍 مشاهده کامل: /req_{req.id}"
        )
        if req.status != ActivationRequest.STATUS_ACTIVATED:
            line += f" | ⚡ فعال‌سازی: /activate_{req.id}"
        msg_lines.append(line)

    msg_lines.append(
        "\n💡 <i>جهت فعال‌سازی هر درخواست و صدور خودکار نام کاربری و رمز عبور، دستور <code>/activate_ID</code> را لمس یا ارسال نمایید.</i>"
    )
    send_message(chat_id, "\n\n".join(msg_lines), reply_markup=get_main_keyboard())


def handle_top_leads(chat_id: int | str) -> None:
    """Lists top-scoring prioritized Instagram leads within the 5-day window."""
    register_subscriber(chat_id)
    from datetime import timedelta
    from django.db.models import Q
    from django.utils import timezone as dj_tz
    from apps.discovery.models import InstagramEngineLead
    from apps.discovery.instagram_scoring import update_lead_scoring

    cutoff_5_days = dj_tz.now() - timedelta(days=5)
    leads_qs = list(
        InstagramEngineLead.objects.filter(
            Q(comment_created_at__gte=cutoff_5_days)
            | Q(comment_created_at__isnull=True, created_at__gte=cutoff_5_days)
        ).order_by("-outreach_score", "-comment_created_at", "-created_at")[:10]
    )

    if not leads_qs:
        send_message(
            chat_id,
            "هیچ لید واجد شرایطی در بازه زمانی ۵ روز اخیر یافت نشد.\n"
            "تنها کامنت‌هایی که طی ۵ روز گذشته ثبت شده باشند بررسی و در صف امتیازدهی قرار می‌گیرند.",
            reply_markup=get_main_keyboard()
        )
        return

    for lead in leads_qs:
        if not lead.outreach_score:
            update_lead_scoring(lead, commit=True)

    leads_qs.sort(key=lambda l: (l.outreach_score, l.comment_created_at or l.created_at), reverse=True)

    msg_lines = [
        "<b>فهرست لیدهای برتر و پرامتیاز اینستاگرام (۱۰ مورد اولویت‌دار زیر ۵ روز):</b>\n"
    ]

    for i, lead in enumerate(leads_qs, 1):
        username = html.escape(lead.username or "نامشخص")
        page_type = "عمومی (پابلیک)" if not lead.is_private else "خصوصی (پرایوت)"

        if lead.comment_intent_type == "QUESTION_INQUIRY":
            intent_label = "سوال و استعلام مشاوره‌ای (اقساط/مراحل)"
        elif lead.comment_intent_type == "DIRECT_SHORT_CTA":
            intent_label = "پیام کوتاه کال‌تواکشن (قیمت/دایرکت)"
        else:
            intent_label = "لید عمومی علاقه‌مند"

        breakdown = lead.score_breakdown or {}
        freshness_label = breakdown.get("age_label") or breakdown.get("freshness_label", "۵ روز اخیر")
        pub_score = breakdown.get("public_score", breakdown.get("public_profile_score", 0))
        intent_score = breakdown.get("intent_score", 0)
        fresh_score = breakdown.get("freshness_score", 0)
        raw_text = lead.comment_text or ""
        comment_text = html.escape(raw_text[:120])
        if len(raw_text) > 120:
            comment_text += "..."

        line = (
            f"{i}. <b>@{username}</b> | <b>امتیاز: {lead.outreach_score} از ۳۵</b>\n"
            f"   وضعیت پیج: {page_type} (امتیاز: {pub_score})\n"
            f"   نوع نیت: {intent_label} (امتیاز: {intent_score})\n"
            f"   تازگی: {freshness_label} (امتیاز: {fresh_score})\n"
            f"   متن کامنت: <i>«{comment_text}»</i>\n"
            f"   وضعیت صف: {lead.status}"
        )
        msg_lines.append(line)

    msg_lines.append(
        "\nفرمول امتیازدهی: پیج پابلیک (۱۵) + نوع کامنت (۱۵) + تازگی تا ۵ روز (۵) = حداکثر ۳۵ امتیاز.\n"
        "سیستم ارسال دایرکت به ترتیب بالاترین امتیاز اقدام به ارسال پیام می‌نماید."
    )

    send_message(chat_id, "\n\n".join(msg_lines), reply_markup=get_main_keyboard())


def handle_single_request(chat_id: int | str, req_id: int) -> None:
    """Shows full details of a specific activation request."""
    register_subscriber(chat_id)
    from apps.accounts.models import ActivationRequest
    from apps.core.jalali import format_jalali_datetime

    req = ActivationRequest.objects.filter(pk=req_id).first()
    if not req:
        send_message(chat_id, f"⚠️ درخواست با شناسه #{req_id} یافت نشد.", reply_markup=get_main_keyboard())
        return

    try:
        dt_str = format_jalali_datetime(req.created_at)
    except Exception:
        dt_str = str(req.created_at)[:16]

    esc_name = html.escape(req.full_name)
    esc_comp = html.escape(req.company_name)
    esc_prob = html.escape(req.problem_statement or "ثبت نشده")

    user_info = ""
    if req.created_user:
        user_info = f"\n🔑 <b>حساب ایجادشده:</b> <code>{req.created_user.username}</code>"

    action_text = ""
    if req.status != ActivationRequest.STATUS_ACTIVATED:
        action_text = f"\n\n⚡ <b>اقدام سریع:</b>\nجهت صدور حساب کاربری و رمز عبور دستور زیر را ارسال نمایید:\n👉 /activate_{req.id}"

    msg = (
        f"📄 <b>جزئیات درخواست فعال‌سازی #{req.id}:</b>\n\n"
        f"👤 <b>نام و نام خانوادگی:</b> <b>{esc_name}</b>\n"
        f"🏢 <b>نام شرکت / برند:</b> <b>{esc_comp}</b>\n"
        f"📞 <b>شماره تماس:</b> <code>{req.phone_number}</code>\n"
        f"📧 <b>ایمیل کاری:</b> <code>{req.email}</code>\n"
        f"👥 <b>اندازه تیم:</b> {html.escape(req.team_size or 'نامشخص')}\n"
        f"📊 <b>وضعیت فعلی:</b> <b>{req.get_status_display()}</b>\n"
        f"📅 <b>زمان ثبت:</b> {dt_str}{user_info}\n\n"
        f"🎯 <b>اهداف و چالش‌ها:</b>\n<i>{esc_prob}</i>"
        f"{action_text}"
    )
    send_message(chat_id, msg, reply_markup=get_main_keyboard())


def handle_quick_activate(chat_id: int | str, req_id: int) -> None:
    """Quickly activates account and business from Telegram bot."""
    register_subscriber(chat_id)
    from apps.accounts.models import ActivationRequest
    from apps.accounts.services import activate_account_from_request

    req = ActivationRequest.objects.filter(pk=req_id).first()
    if not req:
        send_message(chat_id, f"⚠️ درخواست با شناسه #{req_id} یافت نشد.", reply_markup=get_main_keyboard())
        return

    if req.status == ActivationRequest.STATUS_ACTIVATED and req.created_user:
        send_message(
            chat_id,
            f"ℹ️ <b>این درخواست قبلاً فعال شده است!</b>\n"
            f"نام کاربری: <code>{req.created_user.username}</code>\n"
            f"کسب‌وکار: <b>{req.company_name}</b>",
            reply_markup=get_main_keyboard()
        )
        return

    try:
        res = activate_account_from_request(req)
        msg = (
            "✅ <b>حساب کاربری با موفقیت فعال و صادر شد!</b>\n\n"
            f"👤 <b>متقاضی:</b> <b>{html.escape(req.full_name)}</b>\n"
            f"🏢 <b>کسب‌وکار:</b> <b>{html.escape(req.company_name)}</b>\n"
            f"📞 <b>شماره تماس:</b> <code>{req.phone_number}</code>\n"
            f"🔑 <b>نام کاربری:</b> <code>{res['username']}</code>\n"
            f"🔒 <b>رمز عبور موقت:</b> <code>{res['raw_password']}</code>\n"
            f"🌐 <b>لینک ورود به پنل:</b> <code>https://peyda.store/login/</code>\n\n"
            "📌 <i>اطلاعات فوق برای تحویل به مشتری آماده است و رکورد در پنل ادمین جنگو نیز به وضعیت «فعال‌شده» به‌روزرسانی شد.</i>"
        )
        send_message(chat_id, msg, reply_markup=get_main_keyboard())
    except Exception as e:
        log.error("Quick activate failed for #%s: %s", req_id, e, exc_info=True)
        send_message(chat_id, f"❌ خطا در فعال‌سازی: <code>{html.escape(str(e))}</code>", reply_markup=get_main_keyboard())


def handle_luna_status(chat_id: int | str) -> None:
    register_subscriber(chat_id)
    send_message(chat_id, "⏳ <b>در حال ارزیابی زنده وضعیت سرویس و اعتبار API لونا...</b>")
    st = check_luna_api_status()
    esc_msg = html.escape(str(st.get("message", "")))

    if st["ok"]:
        status_line = "🟢 <b>وضعیت: فعال و برخط (دارای اعتبار)</b>"
        action_line = "• مدل در حال پردازش درخواست‌های استخراج، راستی‌آزمایی و پاسخ‌دهی است."
    elif st["credit_exhausted"]:
        status_line = "🔴 <b>وضعیت: اتمام اعتبار (کد ۴۰۲ - Insufficient Credits)</b>"
        action_line = (
            "⚠️ <b>توجه مهم:</b> اعتبار کلید API در پنل سرویس‌دهنده به پایان رسیده است.\n"
            "• موتور استخراج نیازها و تحلیل فرصت‌ها تا زمان افزایش موجودی در حالت انتظار قرار دارد.\n"
            "• لطفاً جهت تداوم فرآیند، حساب هوشیار۲۴ را شارژ فرمایید."
        )
    else:
        status_line = f"🟠 <b>وضعیت: خطا در پاسخ‌دهی (کد {st['status_code']})</b>"
        action_line = f"• پیام دریافتی: <code>{esc_msg}</code>"

    msg = (
        "🧠 <b>گزارش وضعیت سرویس هوش مصنوعی لونا (Luna API):</b>\n\n"
        f"{status_line}\n\n"
        f"🔹 <b>مدل اصلی:</b> <code>{html.escape(str(st.get('model', '')))}</code>\n"
        f"🏢 <b>سرویس‌دهنده:</b> <b>{html.escape(str(st.get('provider', '')))}</b>\n"
        f"🌐 <b>آدرس وب‌سرویس:</b> <code>{html.escape(str(st.get('base_url', '')))}</code>\n"
        f"⚡ <b>تاخیر پاسخ (Latency):</b> <b>{st.get('latency_ms', 0)} ms</b>\n"
        f"📝 <b>پیام سیستم:</b> <i>{esc_msg}</i>\n\n"
        f"{action_line}"
    )
    send_message(chat_id, msg, reply_markup=get_main_keyboard())


def send_long_prompt(chat_id: int | str, header_text: str, prompt_text: str, footer_text: str) -> None:
    """Sends header, chunked prompt text inside <pre>, and footer to avoid Telegram 4096 char limit."""
    send_message(chat_id, header_text)
    time.sleep(0.04)

    chunk_size = 2800
    clean_prompt = prompt_text or ""
    if len(clean_prompt) <= chunk_size:
        esc_chunk = html.escape(clean_prompt)
        send_message(chat_id, f"<pre>{esc_chunk}</pre>")
    else:
        total_parts = (len(clean_prompt) + chunk_size - 1) // chunk_size
        for idx in range(0, len(clean_prompt), chunk_size):
            part_num = (idx // chunk_size) + 1
            chunk = clean_prompt[idx:idx + chunk_size]
            esc_chunk = html.escape(chunk)
            part_msg = f"<b>بخش {part_num} از {total_parts}:</b>\n<pre>{esc_chunk}</pre>"
            send_message(chat_id, part_msg)
            time.sleep(0.05)

    send_message(chat_id, footer_text, reply_markup=get_main_keyboard())


def handle_prompts_list(chat_id: int | str) -> None:
    """Lists all 19 system prompts grouped by category."""
    register_subscriber(chat_id)
    from apps.core.prompts import get_all_prompts_info, ensure_prompts_seeded

    try:
        ensure_prompts_seeded()
    except Exception as e:
        log.warning("ensure_prompts_seeded warning: %s", e)

    all_prompts = get_all_prompts_info()
    if not all_prompts:
        send_message(chat_id, "هیچ پرامپتی در کاتالوگ سامانه یافت نشد.", reply_markup=get_main_keyboard())
        return

    categories: dict[str, list[dict[str, Any]]] = {}
    for p in all_prompts:
        cat = p.get("category", "سایر بخش‌ها")
        categories.setdefault(cat, []).append(p)

    intro = (
        "<b>سامانه مدیریت و پایش پرامپت‌های لونا (gpt-6-luna)</b>\n\n"
        "در این بخش پرامپت‌های تمام ۱۹ نقطه فراخوانی مدل لونا در سراسر ماژول‌های سامانه در دسترس شما قرار دارد.\n"
        "هر پرامپت دارای کاربرد، ورودی، خروجی و ماژول مشخص است و در صورت تغییر توسط شما، به صورت آنی و بلادرنگ در تمام بخش‌های فعال سامانه اعمال خواهد شد.\n\n"
        "فهرست پرامپت‌ها بر اساس دسته‌بندی عملیاتی:\n"
    )
    send_message(chat_id, intro)
    time.sleep(0.04)

    counter = 1
    for cat_name, items in categories.items():
        lines = [f"<b>{html.escape(cat_name)}</b> (تعداد: {len(items)}):"]
        for item in items:
            key = item.get("key", "")
            title = item.get("title", key)
            is_custom = item.get("is_customized", False)
            status_txt = "شخصی‌سازی شده (فعال)" if is_custom else "پیش‌فرض سامانه"
            lines.append(
                f"{counter}. <b>{html.escape(title)}</b>\n"
                f"   کلید سیستمی: <code>{key}</code>\n"
                f"   ماژول: <code>{html.escape(item.get('module_path', ''))}</code>\n"
                f"   وضعیت: <b>{status_txt}</b>\n"
                f"   مشاهده و ویرایش: /prompt_{key}"
            )
            counter += 1
        msg = "\n\n".join(lines)
        send_message(chat_id, msg)
        time.sleep(0.05)

    footer = (
        "<b>راهنمای دستورات پرامپت:</b>\n"
        "• جهت مشاهده جزئیات، شرح کاربرد و متن کامل هر پرامپت دستور <code>/prompt_KEY</code> را ارسال نمایید.\n"
        "• جهت ویرایش تعاملی و ارسال متن جدید دستور <code>/edit_KEY</code> را ارسال فرمایید.\n"
        "• جهت بازگردانی به متن پیش‌فرض کارخانه دستور <code>/resetprompt_KEY</code> را استفاده کنید."
    )
    send_message(chat_id, footer, reply_markup=get_main_keyboard())


def handle_single_prompt(chat_id: int | str, key: str) -> None:
    """Shows complete details, purpose, caller module, status, and full text of a prompt."""
    register_subscriber(chat_id)
    from apps.core.prompts import get_prompt_info

    info = get_prompt_info(key)
    if not info:
        send_message(
            chat_id,
            f"پرامپت با کلید <code>{html.escape(key)}</code> در سامانه یافت نشد.\n\nجهت مشاهده فهرست کلیدهای معتبر از دستور /prompts استفاده نمایید.",
            reply_markup=get_main_keyboard()
        )
        return

    title = info.get("title", key)
    cat = info.get("category", "عمومی")
    module_path = info.get("module_path", "")
    desc = info.get("description", "")
    is_custom = info.get("is_customized", False)
    status_txt = "شخصی‌سازی شده (تغییر یافته توسط مدیر)" if is_custom else "پیش‌فرض اولیه سامانه"
    version = info.get("version", 1)

    time_str = ""
    updated_at = info.get("updated_at")
    if updated_at:
        try:
            from apps.core.jalali import format_jalali_datetime
            time_str = f" | آخرین به‌روزرسانی: {format_jalali_datetime(updated_at)}"
        except Exception:
            time_str = f" | آخرین به‌روزرسانی: {str(updated_at)[:16]}"

    header = (
        f"<b>مشخصات پرامپت: {html.escape(title)}</b>\n\n"
        f"کلید سیستمی: <code>{key}</code>\n"
        f"دسته‌بندی: <b>{html.escape(cat)}</b>\n"
        f"ماژول فراخواننده: <code>{html.escape(module_path)}</code>\n"
        f"وضعیت فعال: <b>{status_txt}</b>\n"
        f"نسخه: <b>{version}</b>{time_str}\n\n"
        f"<b>کاربرد و هدف این پرامپت در سامانه:</b>\n"
        f"<i>{html.escape(desc)}</i>\n\n"
        f"<b>متن کامل پرامپت ارسالی به مدل لونا (gpt-6-luna):</b>"
    )

    prompt_text = info.get("current_prompt", "")

    footer = (
        f"<b>اقدامات مدیریتی پرامپت «{html.escape(title)}»:</b>\n\n"
        f"• ویرایش و ثبت متن جدید: /edit_{key}\n"
        f"• بازگردانی به متن پیش‌فرض کارخانه: /resetprompt_{key}\n"
        f"• بازگشت به فهرست پرامپت‌ها: /prompts"
    )

    send_long_prompt(chat_id, header, prompt_text, footer)


def handle_edit_prompt_start(chat_id: int | str, key: str) -> None:
    """Begins interactive editing state for a prompt."""
    register_subscriber(chat_id)
    from apps.core.prompts import get_prompt_info

    info = get_prompt_info(key)
    if not info:
        send_message(chat_id, f"پرامپت با کلید <code>{html.escape(key)}</code> یافت نشد.", reply_markup=get_main_keyboard())
        return

    USER_STATES[chat_id] = {
        "action": "waiting_prompt_text",
        "key": key
    }

    title = info.get("title", key)
    msg = (
        f"<b>ویرایش پرامپت: {html.escape(title)}</b>\n"
        f"کلید سیستمی: <code>{key}</code>\n"
        f"ماژول: <code>{html.escape(info.get('module_path', ''))}</code>\n\n"
        f"لطفاً متن جدید پرامپت را در قالب یک پیام متنی ارسال فرمایید.\n\n"
        f"توجه مهم: به محض ارسال متن جدید، پرامپت بلافاصله در پایگاه داده و فایل اشتراکی ذخیره شده و تمامی ماژول‌های فعال سامانه از همان لحظه بدون نیاز به راه‌اندازی مجدد، با پرامپت جدید کار خواهند کرد.\n\n"
        f"جهت لغو و انصراف از ویرایش، دستور /cancel را ارسال نمایید."
    )
    send_message(chat_id, msg)


def handle_edit_prompt_save(chat_id: int | str, key: str, new_text: str) -> None:
    """Saves new prompt text and ensures instant propagation across all workers."""
    register_subscriber(chat_id)
    USER_STATES.pop(chat_id, None)
    from apps.core.prompts import set_system_prompt, get_prompt_info

    clean_text = (new_text or "").strip()
    if len(clean_text) < 10:
        send_message(
            chat_id,
            "متن پرامپت ارسال‌شده بسیار کوتاه یا فاقد محتوای معتبر است (حداقل ۱۰ کاراکتر مورد نیاز است). عملیات ذخیره لغو شد.",
            reply_markup=get_main_keyboard()
        )
        return

    ok, message = set_system_prompt(key, clean_text)
    info = get_prompt_info(key) or {}
    title = info.get("title", key)
    version = info.get("version", 1)

    if ok:
        success_msg = (
            f"<b>تغییر پرامپت با موفقیت ذخیره و اعمال شد.</b>\n\n"
            f"عنوان پرامپت: <b>{html.escape(title)}</b>\n"
            f"کلید سیستمی: <code>{key}</code>\n"
            f"نسخه جدید: <b>{version}</b>\n"
            f"تعداد کاراکتر: <b>{len(clean_text)}</b>\n\n"
            f"نتیجه عملیات: <i>{html.escape(message)}</i>\n\n"
            f"پرامپت جدید به صورت بلادرنگ در حافظه و لایه اشتراکی تمامی ورکرها قرار گرفت.\n\n"
            f"مشاهده پرامپت به‌روزشده: /prompt_{key}\n"
            f"بازگشت به فهرست پرامپت‌ها: /prompts"
        )
        send_message(chat_id, success_msg, reply_markup=get_main_keyboard())
    else:
        fail_msg = (
            f"خطا در به‌روزرسانی پرامپت <code>{html.escape(key)}</code>:\n"
            f"<code>{html.escape(message)}</code>"
        )
        send_message(chat_id, fail_msg, reply_markup=get_main_keyboard())


def handle_reset_prompt(chat_id: int | str, key: str) -> None:
    """Resets a prompt to its original system default text."""
    register_subscriber(chat_id)
    from apps.core.prompts import reset_system_prompt, get_prompt_info

    info = get_prompt_info(key)
    if not info:
        send_message(chat_id, f"پرامپت با کلید <code>{html.escape(key)}</code> یافت نشد.", reply_markup=get_main_keyboard())
        return

    title = info.get("title", key)
    ok, message = reset_system_prompt(key)

    msg = (
        f"<b>بازنشانی پرامپت: {html.escape(title)}</b>\n\n"
        f"کلید سیستمی: <code>{key}</code>\n"
        f"نتیجه عملیات: <i>{html.escape(message)}</i>\n\n"
        f"پرامپت با موفقیت به متن پیش‌فرض کارخانه بازگشت و تغییر به صورت آنی در تمام ماژول‌ها اعمال گردید.\n\n"
        f"مشاهده متن فعلی: /prompt_{key}\n"
        f"فهرست تمام پرامپت‌ها: /prompts"
    )
    send_message(chat_id, msg, reply_markup=get_main_keyboard())


def handle_start(chat_id: int | str) -> None:
    register_subscriber(chat_id)
    welcome_text = (
        "سلام! به بات هوشمند پایش و مدیریت خوش آمدید.\n\n"
        "این بات وظیفه دریافت و اعتبارسنجی کانفیگ‌ها، پایش زنده اکانت‌ها، نظارت بر سلامت و اعتبار سرویس هوش مصنوعی لونا (Luna API) و مدیریت متمرکز پرامپت‌ها را بر عهده دارد.\n\n"
        "امکانات کلیدی:\n"
        "• مدیریت پرامپت‌های لونا (LLM): مشاهده، ویرایش و بازنشانی بلادرنگ ۱۹ پرامپت فعال\n"
        "• وضعیت API و اعتبار لونا: بررسی برخط اعتبار و سلامت مدل gpt-6-luna\n"
        "• وضعیت کانفیگ‌ها و سرور: مشاهده پینگ و اتصالات پروکسی‌ها\n"
        "• درخواست‌های فعال‌سازی: مدیریت متقاضیان و صدور حساب کاربری\n"
        "• ارسال کانفیگ: ارسال لینک‌های پروکسی جهت تست و اختصاص به اکانت‌ها\n\n"
        "پروتکل‌های مجاز پروکسی: vless:// | vmess:// | trojan:// | ss://"
    )
    send_message(chat_id, welcome_text, reply_markup=get_main_keyboard())


def handle_help(chat_id: int | str) -> None:
    help_text = (
        "❓ <b>راهنمای ارسال کانفیگ:</b>\n\n"
        "شما می‌توانید هر تعداد کانفیگ را به صورت متنی بفرستید (هر کانفیگ در یک خط).\n\n"
        "<b>نمونه کانفیگ‌های قابل قبول:</b>\n"
        "• <code>vless://uuid@server:443?security=reality&amp;...#Tag</code>\n"
        "• <code>vmess://base64_data...</code>\n"
        "• <code>trojan://password@server:443#Tag</code>\n"
        "• <code>ss://base64_data@server:port#Tag</code>\n\n"
        "⚠️ <i>کانفیگ‌هایی که قطع باشند یا پینگ منفی یک بدهند هرگز به سیستم اضافه نخواهند شد.</i>"
    )
    send_message(chat_id, help_text, reply_markup=get_main_keyboard())


def handle_status(chat_id: int | str) -> None:
    register_subscriber(chat_id)
    from apps.discovery.models import ProxyConfig, InstagramEngineAccount, TwitterEngineAccount

    # Section 0: Luna AI API Status
    luna_st = check_luna_api_status()
    if luna_st["ok"]:
        luna_badge = f"🟢 فعال ({luna_st['latency_ms']}ms)"
    elif luna_st["credit_exhausted"]:
        luna_badge = "🔴 اتمام اعتبار (کد ۴۰۲)"
    else:
        luna_badge = f"🟠 خطا ({luna_st['status_code']})"

    proxies = list(ProxyConfig.objects.all().order_by("local_port"))
    active_count = sum(1 for p in proxies if p.status == "ACTIVE" and p.ping_ms > 0)

    msg_lines = [
        "📊 <b>وضعیت جامع سیستم و سرور:</b>\n",
        f"🧠 <b>سرویس هوش مصنوعی لونا (gpt-6-luna):</b> {luna_badge}\n"
        f"   سرویس‌دهنده: <b>{html.escape(str(luna_st.get('provider', '')))}</b> | وضعیت: <i>{html.escape(str(luna_st.get('message', '')))}</i>\n"
    ]

    # Section 1: Proxy configs
    if not proxies:
        msg_lines.append(
            "🌐 <b>مخزن کانفیگ‌های اختصاصی:</b>\n"
            "📭 <i>هنوز هیچ کانفیگ اختصاصی اضافه‌ای در دیتابیس ثبت نشده است.</i>\n"
            "• اکانت‌ها در حال حاضر از پروکسی پیش‌فرض سیستم (<code>socks5h://xray:1080</code>) استفاده می‌کنند.\n"
            "• جهت اختصاص آی‌پی‌های مجزا، کافیست لینک‌های کانفیگ سالم خود را همین‌جا ارسال کنید.\n"
            "• ⚠️ <i>کانفیگ‌های قطع‌شده (پینگ -۱) خودکار رد یا پاکسازی می‌شوند تا هیچ کانفیگ خرابی در سیستم باقی نماند.</i>\n"
        )
    else:
        msg_lines.append(
            f"🌐 <b>مخزن کانفیگ‌های اختصاصی (تعداد: {len(proxies)} | سالم: {active_count}):</b>"
        )
        for i, p in enumerate(proxies, 1):
            status_icon = "🟢" if p.status == "ACTIVE" and p.ping_ms > 0 else "🔴"
            ping_str = f"{p.ping_ms:.0f}ms" if p.ping_ms > 0 else "قطع (-۱)"
            assigned_str = f"👉 متصل به <code>@{p.assigned_to}</code>" if p.assigned_to else "آزاد (آماده انتساب)"
            msg_lines.append(
                f"{i}. {status_icon} <b>{p.tag or p.server}</b>\n"
                f"   پروتکل: <code>{p.protocol.upper()}</code> | پینگ: <b>{ping_str}</b>\n"
                f"   پورت اختصاصی: <code>{p.local_port or '—'}</code> | {assigned_str}"
            )
        msg_lines.append("")

    # Section 2: Accounts and their connected proxies
    ig_accs = list(InstagramEngineAccount.objects.all().order_by("id"))
    x_accs = list(TwitterEngineAccount.objects.all().order_by("id"))

    msg_lines.append("👥 <b>وضعیت اتصال اکانت‌ها به پروکسی و دیوایس:</b>")

    msg_lines.append("\n📸 <b>اکانت‌های اینستاگرام:</b>")
    if not ig_accs:
        msg_lines.append("<i>هیچ اکانت اینستاگرامی ثبت نشده است.</i>")
    for a in ig_accs:
        st_icon = "🟢" if a.status == "ACTIVE" else "🟠"
        dev_name = (a.device_settings or {}).get("device_name", "Sony Xperia 1 V / S24 Ultra")
        proxy_display = a.proxy_url or "socks5h://xray:1080 (پیش‌فرض)"
        msg_lines.append(
            f"• {st_icon} <b>@{a.username}</b> ({a.get_status_display()})\n"
            f"   📱 دیوایس: <code>{dev_name}</code>\n"
            f"   🌐 پروکسی: <code>{proxy_display}</code>\n"
        )

    msg_lines.append("🐦 <b>اکانت‌های توییتر / X:</b>")
    if not x_accs:
        msg_lines.append("<i>هیچ اکانت توییتری ثبت نشده است.</i>")
    for a in x_accs:
        st_icon = "🟢" if a.status == "ACTIVE" else "🟡" if a.status == "COOLDOWN" else "🟠"
        proxy_display = a.proxy_url or "socks5h://xray:1080 (پیش‌فرض)"
        msg_lines.append(
            f"• {st_icon} <b>@{a.username}</b> ({a.get_status_display()})\n"
            f"   🌐 پروکسی: <code>{proxy_display}</code>\n"
        )

    send_message(chat_id, "\n".join(msg_lines), reply_markup=get_main_keyboard())


def handle_accounts_status(chat_id: int | str) -> None:
    handle_status(chat_id)


def handle_prune(chat_id: int | str) -> None:
    send_message(chat_id, "⏳ <b>در حال ارزیابی زنده پینگ تمام کانفیگ‌ها و پاکسازی موارد قطع شده...</b>\nلطفاً چند لحظه شکیبا باشید.")
    removed = prune_dead_configs()
    assign_res = assign_proxies_to_accounts()

    if removed:
        msg = (
            f"🧹 <b>عملیات پاکسازی با موفقیت انجام شد!</b>\n\n"
            f"❌ تعداد <b>{len(removed)}</b> کانفیگ غیرفعال (پینگ -۱) از دیتابیس و سرور به طور کامل حذف شدند:\n"
        )
        for r in removed:
            msg += f"• <code>{r.get('tag')}</code> ({r.get('protocol')})\n"
        msg += f"\n🔄 پروکسی اکانت‌ها مجدداً با کانفیگ‌های سالم بالانس شد (تعداد اکانت‌های متصل: {assign_res['total_assigned']})."
    else:
        msg = "✅ <b>همه کانفیگ‌های موجود در سیستم سالم هستند و هیچ کانفیگ خرابی یافت نشد!</b>"

    send_message(chat_id, msg, reply_markup=get_main_keyboard())


def handle_incoming_text(chat_id: int | str, text: str) -> None:
    # Match any proxy protocol links
    pattern = r"(vless://[^\s]+|vmess://[^\s]+|trojan://[^\s]+|ss://[^\s]+)"
    matches = re.findall(pattern, text, re.IGNORECASE)

    if not matches:
        send_message(
            chat_id,
            "⚠️ <b>فرمت پیام شناسایی نشد!</b>\n"
            "پیام شما شامل لینک کانفیگ معتبر نبود. لطفاً مطمئن شوید کانفیگ با یکی از فرمت‌های <code>vless://</code>، <code>vmess://</code>، <code>trojan://</code> یا <code>ss://</code> آغاز می‌شود.",
            reply_markup=get_main_keyboard()
        )
        return

    send_message(
        chat_id,
        f"🔍 <b>تعداد {len(matches)} کانفیگ شناسایی شد.</b>\nدر حال بررسی ساختار و تست زنده پینگ اتصال (حداکثر ۲ ثانیه به ازای هر کانفیگ)... ⏳",
        reply_markup=get_main_keyboard()
    )

    success_count = 0
    fail_count = 0

    for link in matches:
        link = link.strip()
        res = add_proxy_config(link)
        if res["success"]:
            success_count += 1
            reply = (
                f"✅ <b>کانفیگ با موفقیت متصل و ثبت شد!</b>\n\n"
                f"🏷 نام/تگ: <code>{res.get('tag')}</code>\n"
                f"🔹 پروتکل: <code>{res.get('protocol', '').upper()}</code>\n"
                f"⚡ پینگ واقعی: <b>{res.get('ping_ms')} ms</b>\n"
                f"🌐 پورت اختصاصی: <code>{res.get('proxy_url')}</code>"
            )
        else:
            fail_count += 1
            reply = (
                f"❌ <b>کانفیگ ناموفق (پینگ منفی یک / بالای ۲۰۰۰ms):</b>\n\n"
                f"⚠️ دلیل: {res.get('message')}\n"
                f"🚫 <i>این کانفیگ ذخیره نشد و از دیتابیس حذف گردید.</i>"
            )
        send_message(chat_id, reply)
        time.sleep(0.1)

    summary = (
        f"🏁 <b>نتیجه بررسی نهایی:</b>\n"
        f"🟢 موفق و فعال: <b>{success_count}</b>\n"
        f"🔴 ناموفق / حذف شده: <b>{fail_count}</b>"
    )
    send_message(chat_id, summary, reply_markup=get_main_keyboard())


def periodic_health_check_worker() -> None:
    """Background worker that periodically monitors Luna API credits and cleans up dead proxies."""
    last_credit_alert_time = 0.0
    last_credit_state_exhausted = False
    cycle_counter = 0

    while True:
        try:
            time.sleep(300)  # Check every 5 minutes
            cycle_counter += 1

            # 1. Periodic Luna API Credit & Health Check
            try:
                luna_st = check_luna_api_status()
                is_exhausted = luna_st.get("credit_exhausted", False)
                now_ts = time.time()

                if is_exhausted:
                    # Alert if state just changed to exhausted, or once every 3 hours as reminder
                    if not last_credit_state_exhausted or (now_ts - last_credit_alert_time > 10800):
                        last_credit_alert_time = now_ts
                        last_credit_state_exhausted = True
                        alert_text = (
                            "🚨 <b>هشدار مهم: اتمام اعتبار API لونا</b>\n\n"
                            "اعتبار سرویس هوش مصنوعی لونا در سرویس‌دهنده به پایان رسیده است.\n\n"
                            f"🔹 <b>مدل:</b> <code>{html.escape(str(luna_st.get('model', '')))}</code>\n"
                            f"🏢 <b>سرویس‌دهنده:</b> <b>{html.escape(str(luna_st.get('provider', '')))}</b>\n"
                            f"⚠️ <b>خطا:</b> <code>{html.escape(str(luna_st.get('message', '')))}</code>\n\n"
                            "📌 <i>لطفاً نسبت به شارژ اعتبار حساب در هوشیار۲۴ اقدام فرمایید تا فرآیند کشف و پردازش فرصت‌ها بدون وقفه ادامه یابد.</i>"
                        )
                        log.warning("Luna credit exhausted! Broadcasting alert to subscribers...")
                        broadcast_message(alert_text, reply_markup=get_main_keyboard())
                else:
                    if last_credit_state_exhausted and luna_st.get("ok"):
                        last_credit_state_exhausted = False
                        restored_text = (
                            "✅ <b>اطلاعیه: اعتبار API لونا شارژ شد</b>\n\n"
                            "سرویس هوش مصنوعی لونا مجدداً برخط و فعال گردید.\n\n"
                            f"🔹 <b>مدل:</b> <code>{html.escape(str(luna_st.get('model', '')))}</code>\n"
                            f"⚡ <b>تاخیر پاسخ:</b> <b>{luna_st.get('latency_ms', 0)} ms</b>\n"
                            "🚀 موتور تحلیل نیازها و پاسخ‌دهی هوشمند به چرخه عملیاتی بازگشت."
                        )
                        log.info("Luna credit restored! Broadcasting notification...")
                        broadcast_message(restored_text, reply_markup=get_main_keyboard())
            except Exception as luna_exc:
                log.warning("Periodic Luna health check exception: %s", luna_exc)

            # 2. Prune dead configs every 20 minutes (every 4th cycle)
            if cycle_counter % 4 == 0:
                log.info("Running scheduled proxy health check & dead config purge...")
                prune_dead_configs()

        except Exception as exc:
            log.warning("Periodic health check exception: %s", exc)


def run_bot() -> None:
    """Main long-polling loop for Telegram bot."""
    log.info("Starting Telegram Config Management Bot...")
    last_update_id = 0

    # Start periodic health check in background thread
    t = threading.Thread(target=periodic_health_check_worker, daemon=True)
    t.start()

    while True:
        try:
            updates = tg_request("getUpdates", {"offset": last_update_id + 1, "timeout": 25}, timeout=35)
            if not updates or not updates.get("ok"):
                time.sleep(3)
                continue

            for u in updates.get("result", []):
                last_update_id = max(last_update_id, u["update_id"])
                msg = u.get("message")
                if not msg:
                    continue

                chat_id = msg.get("chat", {}).get("id")
                text = (msg.get("text") or "").strip()

                if not chat_id or not text:
                    continue

                register_subscriber(chat_id)
                log.info("Received message from chat %s: '%s'", chat_id, text[:40])

                try:
                    # 1. Check user interactive state (e.g. waiting for prompt text input)
                    user_st = USER_STATES.get(chat_id)
                    if user_st and user_st.get("action") == "waiting_prompt_text":
                        if text in ("/cancel", "انصراف", "لغو"):
                            USER_STATES.pop(chat_id, None)
                            send_message(chat_id, "فرآیند ویرایش پرامپت لغو گردید.", reply_markup=get_main_keyboard())
                            continue
                        p_key = user_st.get("key", "")
                        handle_edit_prompt_save(chat_id, p_key, text)
                        continue

                    if text in ("/cancel", "انصراف", "لغو"):
                        USER_STATES.pop(chat_id, None)
                        send_message(chat_id, "عملیات خاصی در جریان نیست.", reply_markup=get_main_keyboard())
                        continue

                    if text == "/start":
                        handle_start(chat_id)
                    elif text in ("/help", "❓ راهنمای ارسال کانفیگ"):
                        handle_help(chat_id)
                    elif text in ("/status", "📊 وضعیت کانفیگ‌ها و سرور"):
                        handle_status(chat_id)
                    elif text in ("/accounts", "👥 اکانت‌ها و پروکسی‌های متصل"):
                        handle_accounts_status(chat_id)
                    elif text in ("/prune", "🔄 تست مجدد و پاکسازی خرابی‌ها"):
                        handle_prune(chat_id)
                    elif text in ("/luna", "/ai", "🧠 وضعیت API و اعتبار لونا", "وضعیت لونا", "اعتبار لونا"):
                        handle_luna_status(chat_id)
                    elif text in ("/requests", "/demo", "📋 درخواست‌های فعال‌سازی", "درخواست‌ها", "درخواست های فعال سازی"):
                        handle_activation_requests(chat_id)
                    elif text in ("/topleads", "/leads", "/outreach", "لیدهای پرامتیاز اینستاگرام", "لیدهای اینستاگرام"):
                        handle_top_leads(chat_id)
                    elif text in ("/prompts", "/llm", "مدیریت پرامپت‌های لونا (LLM)", "پرامپت‌ها", "پرامپت ها"):
                        handle_prompts_list(chat_id)
                    elif text.startswith("/prompt_"):
                        p_key = text.replace("/prompt_", "").strip()
                        handle_single_prompt(chat_id, p_key)
                    elif text.startswith("/edit_"):
                        p_key = text.replace("/edit_", "").strip()
                        handle_edit_prompt_start(chat_id, p_key)
                    elif text.startswith("/resetprompt_"):
                        p_key = text.replace("/resetprompt_", "").strip()
                        handle_reset_prompt(chat_id, p_key)
                    elif text.startswith("/setprompt_"):
                        raw = text.replace("/setprompt_", "", 1).strip()
                        parts = raw.split(None, 1)
                        if len(parts) == 2:
                            handle_edit_prompt_save(chat_id, parts[0].strip(), parts[1].strip())
                        else:
                            send_message(
                                chat_id,
                                "فرمت دستور نادرست است. ساختار صحیح:\n/setprompt_KEY متن_جدید\nیا از دستور /edit_KEY برای ویرایش تعاملی استفاده فرمایید.",
                                reply_markup=get_main_keyboard()
                            )
                    elif text.startswith("/req_"):
                        try:
                            r_id = int(text.replace("/req_", "").strip())
                            handle_single_request(chat_id, r_id)
                        except Exception as parse_err:
                            send_message(chat_id, f"⚠️ شناسه درخواست نامعتبر است: {parse_err}")
                    elif text.startswith("/activate_"):
                        try:
                            r_id = int(text.replace("/activate_", "").strip())
                            handle_quick_activate(chat_id, r_id)
                        except Exception as parse_err:
                            send_message(chat_id, f"⚠️ شناسه درخواست نامعتبر است: {parse_err}")
                    else:
                        handle_incoming_text(chat_id, text)
                except Exception as handler_exc:
                    log.error("Handler error for chat %s: %s", chat_id, handler_exc, exc_info=True)
                    send_message(
                        chat_id,
                        f"⚠️ <b>خطایی در پردازش رخ داد:</b>\n<code>{html.escape(str(handler_exc))}</code>",
                        reply_markup=get_main_keyboard()
                    )

        except Exception as exc:
            log.warning("Bot polling loop error: %s", exc)
            time.sleep(4)


if __name__ == "__main__":
    run_bot()
