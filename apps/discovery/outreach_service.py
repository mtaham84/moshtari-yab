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


def check_and_sync_lead_replies(tenant_ids: List[str]) -> Dict[str, Any]:
    """
    Checks direct message inboxes of all SENDER accounts for given tenants.
    Detects if any lead has replied, saves their response, and marks lead as REPLIED.
    """
    from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
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
        if sender.proxy_url:
            cl.set_proxy(sender.proxy_url)

        try:
            apply_device_profile_to_client(cl, sender)
            cl.login_by_sessionid(sessionid)
            current_user_id = str(cl.user_id)

            # Fetch recent 25 direct message threads
            threads = cl.direct_threads(amount=25)
            for th in threads:
                messages = getattr(th, "messages", [])
                if not messages:
                    continue

                # Find any message in this thread from the customer
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
                    # Auto-create lead so any customer who replied is instantly visible
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

                    # Update lead if it's a new reply or unreplied state
                    if not lead.has_replied or lead.reply_text != msg_text:
                        lead.has_replied = True
                        lead.status = "REPLIED"
                        lead.reply_text = msg_text
                        lead.reply_at = msg_time
                        lead.sender_account = lead.sender_account or sender.username

                        history = list(lead.conversation_history or [])
                        history.append({
                            "sender": lead.username,
                            "direction": "in",
                            "text": msg_text,
                            "time": msg_time.isoformat() if hasattr(msg_time, "isoformat") else str(msg_time),
                        })
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


def send_second_followup_message(
    lead_id: int,
    sender_username: str,
    message_text: str,
) -> Dict[str, Any]:
    """
    Sends a follow-up or second message to a lead using the chosen SENDER account.
    Allows seamlessly switching accounts (e.g. from consultant Raha to specialist Pania).
    """
    from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
    from apps.discovery.device_emulator import apply_device_profile_to_client
    from instagrapi import Client

    lead = InstagramEngineLead.objects.filter(pk=lead_id).first()
    if not lead:
        return {"success": False, "error": "مشتری مورد نظر یافت نشد."}

    sender = InstagramEngineAccount.objects.filter(
        username__iexact=sender_username,
        role="SENDER",
    ).first()
    if not sender:
        return {"success": False, "error": f"اکانت فرستنده @{sender_username} یافت نشد."}

    if not sender.session_json:
        return {"success": False, "error": f"اکانت @{sender_username} فاقد کوکی سشن معتبر است."}

    cookies = sender.session_json.get("cookies", {})
    sess_id = cookies.get("sessionid")
    if not sess_id:
        return {"success": False, "error": f"کوکی sessionid برای @{sender_username} ثبت نشده است."}

    cl = Client()
    if sender.proxy_url:
        cl.set_proxy(sender.proxy_url)

    try:
        apply_device_profile_to_client(cl, sender)
        cl.login_by_sessionid(sess_id)

        target_pk = int(lead.ig_user_pk) if lead.ig_user_pk and str(lead.ig_user_pk).isdigit() else None
        if not target_pk:
            target_pk = cl.user_id_from_username(lead.username)

        # Dispatch follow-up direct
        cl.direct_send(message_text, user_ids=[target_pk])

        now = timezone.now()
        lead.second_message_text = message_text
        lead.second_sender_username = sender.username
        lead.second_message_at = now
        lead.status = "FOLLOWUP_SENT"

        history = list(lead.conversation_history or [])
        history.append({
            "sender": sender.username,
            "direction": "out",
            "role": "followup",
            "text": message_text,
            "time": now.isoformat(),
        })
        lead.conversation_history = history
        lead.save(update_fields=[
            "second_message_text", "second_sender_username", "second_message_at",
            "status", "conversation_history", "updated_at"
        ])

        sender.daily_action_count += 1
        sender.last_action_at = now
        sender.save(update_fields=["daily_action_count", "last_action_at"])

        return {
            "success": True,
            "lead": lead.username,
            "sender": sender.username,
            "sent_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as exc:
        logger.exception("Error sending follow-up message: %s", exc)
        return {"success": False, "error": str(exc)}
