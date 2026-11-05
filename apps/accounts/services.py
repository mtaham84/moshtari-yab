import html
import logging
import os
import secrets
import string
from typing import Any

from django.contrib.auth import get_user_model

from apps.accounts.models import ActivationRequest
from apps.businesses.models import Business
from apps.billing.models import Wallet
from shared.telegram_alerts import load_subscribers, proxy_candidates

logger = logging.getLogger(__name__)
User = get_user_model()


def get_subscribers() -> set[int]:
    return load_subscribers()


def send_telegram_alert(text: str, reply_markup: dict[str, Any] | None = None) -> bool:
    """
    Sends message to all bot subscribers using requests via local proxy or direct connection.
    """
    import requests

    token = os.environ.get("CONFIG_BOT_TOKEN", "").strip()
    if not token:
        logger.warning("CONFIG_BOT_TOKEN is not set; skipping Telegram alert.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    candidates = proxy_candidates()
    # Once a route works, try it first for the remaining chats so one dead
    # proxy doesn't add a timeout per subscriber.
    working: str | None | bool = False

    subscribers = get_subscribers()
    success = False

    for chat_id in subscribers:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        ordered = candidates if working is False else [working] + [p for p in candidates if p != working]
        for proxy_url in ordered:
            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            try:
                r = requests.post(url, json=payload, proxies=proxies, timeout=10)
                res = r.json()
                if res.get("ok"):
                    success = True
                    working = proxy_url
                    break
            except Exception:
                logger.debug("Telegram alert via %s failed", proxy_url or "direct", exc_info=True)
                continue

    return success


def notify_new_activation_request(req: ActivationRequest) -> bool:
    """
    Broadcasts newly submitted activation request to admin Telegram chats.
    """
    esc_name = html.escape(req.full_name)
    esc_comp = html.escape(req.company_name)
    esc_phone = html.escape(req.phone_number)
    esc_email = html.escape(req.email)
    esc_team = html.escape(req.team_size or "نامشخص")
    esc_prob = html.escape(req.problem_statement or "توضیحاتی ثبت نشده است")

    msg = (
        "📬 <b>درخواست جدید فعال‌سازی و دمو (مشتری جدید)!</b>\n\n"
        f"👤 <b>متقاضی:</b> <b>{esc_name}</b>\n"
        f"🏢 <b>کسب‌وکار / شرکت:</b> <b>{esc_comp}</b>\n"
        f"📞 <b>شماره تماس:</b> <code>{esc_phone}</code>\n"
        f"📧 <b>ایمیل کاری:</b> <code>{esc_email}</code>\n"
        f"👥 <b>اندازه تیم:</b> <b>{esc_team}</b>\n\n"
        f"🎯 <b>هدف و شرح نیازها:</b>\n<i>{esc_prob}</i>\n\n"
        f"🆔 <b>شناسه درخواست:</b> <code>#{req.id}</code>\n"
        f"⚡ <i>جهت فعال‌سازی و صدور حساب، می‌توانید از دستور <code>/activate_{req.id}</code> در همین ربات یا پنل ادمین جنگو استفاده نمایید.</i>"
    )
    return send_telegram_alert(msg)


def generate_secure_password(length: int = 10) -> str:
    """Generates an alphanumeric password that is easy to read and type."""
    alphabet = string.ascii_letters + string.digits + "!@#$"
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in password)
                and any(c.isupper() for c in password)
                and any(c.isdigit() for c in password)):
            return password


def activate_account_from_request(req: ActivationRequest, password: str | None = None) -> dict[str, Any]:
    """
    Creates a User, Business, and Wallet for this activation request, marks it ACTIVATED.
    """
    email = req.email.strip().lower()
    first_name = ""
    last_name = ""
    parts = req.full_name.strip().split(None, 1)
    if len(parts) == 2:
        first_name, last_name = parts[0], parts[1]
    elif len(parts) == 1:
        first_name = parts[0]

    raw_password = password or generate_secure_password(10)

    user = User.objects.filter(email__iexact=email).first()
    if not user:
        user = User.objects.filter(username__iexact=email).first()

    created_new_user = False
    if not user:
        user = User.objects.create_user(
            username=email,
            email=email,
            password=raw_password,
            first_name=first_name,
            last_name=last_name
        )
        created_new_user = True
    else:
        # Update password if explicitly provided
        if password:
            user.set_password(password)
            user.save()

    # Create or update Business
    business, _ = Business.objects.get_or_create(
        user=user,
        defaults={
            "name": req.company_name,
            "business_domain": req.problem_statement[:150] if req.problem_statement else "عمومی",
            "description": req.problem_statement or "",
        }
    )

    # Ensure Wallet exists
    Wallet.objects.get_or_create(
        business=business,
        defaults={"balance_toman": 50000}  # Initial welcome balance
    )

    req.status = ActivationRequest.STATUS_ACTIVATED
    req.created_user = user
    req.created_business = business
    req.save()

    return {
        "user": user,
        "business": business,
        "username": user.username,
        "raw_password": raw_password,
        "created_new_user": created_new_user,
    }
