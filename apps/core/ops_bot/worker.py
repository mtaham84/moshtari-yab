"""The bot's polling loop, the text command router and the periodic proxy health check."""
from __future__ import annotations

import html
import logging
import re
import threading
import time

from xray.multi_proxy_manager import add_proxy_config

from apps.core.ops_bot.leads import handle_activation_requests, handle_quick_activate, handle_single_request, handle_top_leads
from apps.core.ops_bot.prompts import USER_STATES, handle_edit_prompt_save, handle_edit_prompt_start, handle_prompts_list, handle_reset_prompt, handle_single_prompt
from apps.core.ops_bot.status import check_luna_api_status, get_main_keyboard, handle_accounts_status, handle_help, handle_luna_status, handle_prune, handle_start, handle_status
from apps.core.ops_bot.telegram_api import broadcast_message, register_subscriber, send_message, tg_request

log = logging.getLogger("telegram_config_bot")


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
                log.warning("Periodic Luna health check exception: %s", luna_exc, exc_info=True)

            # 2. Prune dead & slow (>2000ms) configs every 5 minutes (every cycle)
            try:
                log.info("Running 5-minute scheduled proxy health check & dead config purge...")
                from apps.discovery.proxies.assignment import prune_dead_configs
                removed = prune_dead_configs()
                if removed:
                    log.info("5-minute purge removed %d dead/slow configs: %s", len(removed), [r.get("tag") for r in removed])
            except Exception as prune_exc:
                log.warning("Periodic proxy prune exception: %s", prune_exc, exc_info=True)

        except Exception as exc:
            log.warning("Periodic health check exception: %s", exc, exc_info=True)


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
                            log.warning("Bot command %s failed", text, exc_info=True)
                            send_message(chat_id, f"⚠️ شناسه درخواست نامعتبر است: {parse_err}")
                    elif text.startswith("/activate_"):
                        try:
                            r_id = int(text.replace("/activate_", "").strip())
                            handle_quick_activate(chat_id, r_id)
                        except Exception as parse_err:
                            log.warning("Bot command %s failed", text, exc_info=True)
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
            log.warning("Bot polling loop error: %s", exc, exc_info=True)
            time.sleep(4)
