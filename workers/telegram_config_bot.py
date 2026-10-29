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

import json
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
sys.path.insert(0, "/root/moshtari-yab")
sys.path.insert(0, "/home/taha/HDD/customerweb")
from pathlib import Path
for env_candidate in [Path("/root/moshtari-yab/.env"), Path(__file__).resolve().parent.parent / ".env"]:
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
    """Sends a formatted message to a Telegram chat."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    tg_request("sendMessage", payload, timeout=12)


def get_main_keyboard() -> dict[str, Any]:
    """Returns main menu reply keyboard in Persian."""
    return {
        "keyboard": [
            [{"text": "📊 وضعیت کانفیگ‌ها و سرور"}, {"text": "👥 اکانت‌ها و پروکسی‌های متصل"}],
            [{"text": "🔄 تست مجدد و پاکسازی خرابی‌ها"}, {"text": "❓ راهنمای ارسال کانفیگ"}]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }


def handle_start(chat_id: int | str) -> None:
    welcome_text = (
        "👋 <b>سلام! به بات هوشمند مدیریت کانفیگ و پروکسی خوش آمدید.</b>\n\n"
        "این بات وظیفه دریافت، اعتبارسنجی زنده پینگ و توزیع اختصاصی کانفیگ‌ها بین اکانت‌های اینستاگرام و توییتر را بر عهده دارد.\n\n"
        "📌 <b>نحوه کار:</b>\n"
        "• کافیست لینک‌های کانفیگ خود را (یک یا چند لینک) در همین چت ارسال نمایید.\n"
        "• بات فوراً فرمت را چک کرده و پینگ واقعی اتصال را می‌سنجد.\n"
        "• کانفیگ‌های سالم با پینگ مثبت ثبت شده و یک پورت مجزا دریافت می‌کنند.\n"
        "• کانفیگ‌های با پینگ منفی یک (-۱) خودکار رد و از دیتابیس حذف می‌شوند.\n\n"
        "پروتکل‌های مجاز: <code>vless://</code> | <code>vmess://</code> | <code>trojan://</code> | <code>ss://</code>"
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
    from apps.discovery.models import ProxyConfig, InstagramEngineAccount, TwitterEngineAccount

    proxies = list(ProxyConfig.objects.all().order_by("local_port"))
    active_count = sum(1 for p in proxies if p.status == "ACTIVE" and p.ping_ms > 0)

    msg_lines = [
        "📊 <b>وضعیت جامع کانفیگ‌ها و سرور:</b>\n"
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
    """Background worker that periodically cleans up dead proxies every 20 minutes."""
    while True:
        try:
            time.sleep(1200)  # 20 minutes
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

                log.info("Received message from chat %s: '%s'", chat_id, text[:40])

                try:
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
                    else:
                        handle_incoming_text(chat_id, text)
                except Exception as handler_exc:
                    log.error("Handler error for chat %s: %s", chat_id, handler_exc, exc_info=True)
                    send_message(
                        chat_id,
                        f"⚠️ <b>خطایی در پردازش رخ داد:</b>\n<code>{handler_exc}</code>",
                        reply_markup=get_main_keyboard()
                    )

        except Exception as exc:
            log.warning("Bot polling loop error: %s", exc)
            time.sleep(4)


if __name__ == "__main__":
    run_bot()
