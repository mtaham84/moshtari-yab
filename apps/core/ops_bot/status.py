"""Bot commands for system status, the Luna LLM API, accounts and proxy pruning, and the main keyboard."""
from __future__ import annotations

import html
import logging
import time
from typing import Any


from apps.core.ops_bot.telegram_api import register_subscriber, send_message

log = logging.getLogger("telegram_config_bot")


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
            except ValueError:   # error body is not JSON
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
        log.warning("Luna API status check failed", exc_info=True)
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
    from apps.discovery.proxies.assignment import assign_proxies_to_accounts, prune_dead_configs
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
