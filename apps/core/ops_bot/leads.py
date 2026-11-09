"""Bot commands for activation requests and top leads."""
from __future__ import annotations

import html
import logging


from apps.core.ops_bot.status import get_main_keyboard
from apps.core.ops_bot.telegram_api import register_subscriber, send_message

log = logging.getLogger("telegram_config_bot")


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
        except (TypeError, ValueError, AttributeError):   # not a datetime
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
    except (TypeError, ValueError, AttributeError):   # not a datetime
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
