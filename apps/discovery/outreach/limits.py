"""Sender rate tiers (Dealism benchmarks), effective daily limits, and the guard for manual DMs from the panel."""
import logging
from typing import Optional
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


def get_effective_sender_limits(account, fallback_daily: Optional[int] = None) -> tuple[int, int]:
    """
    Daily/hourly DM caps for a SENDER account.
    Uses the account's own limits (or the fallback), but never above the warm-up ceiling of its age tier,
    e.g. a NEW account can never exceed 10 DMs/day and 2 DMs/hour even if its daily_limit is 18.
    """
    tier = get_tier_specs(getattr(account, "account_tier", None))
    daily = min(getattr(account, "daily_limit", None) or fallback_daily or tier["default_daily"], tier["max_daily"])
    hourly = min(getattr(account, "hourly_limit", None) or tier["hourly_speed"], tier["hourly_speed"])
    return daily, hourly


BLOCKED_SENDER_STATUSES = ("COOLDOWN", "CHALLENGED", "BANNED", "DISABLED")


def manual_dm_block_reason(account, *, now=None) -> str:
    """Why an operator DM from this account must not go out right now ("" = OK).

    The same rules the outreach dispatcher respects: account state and
    cooldown, the post-IP-change settle period (next_outreach_at), the daily
    cap of its tier and the operating window.
    """
    from zoneinfo import ZoneInfo

    from apps.discovery.anti_ban import in_operating_window

    now = now or timezone.now()
    if account.status in BLOCKED_SENDER_STATUSES:
        return f"اکانت در وضعیت {account.get_status_display()} است."
    if account.cooldown_until and account.cooldown_until > now:
        return "اکانت در دورهٔ استراحت (cooldown) است."
    if account.next_outreach_at and account.next_outreach_at > now:
        mins = max(1, int((account.next_outreach_at - now).total_seconds() // 60))
        return f"تا {mins} دقیقهٔ دیگر (بعد از تغییر IP یا فاصلهٔ ارسال) دایرکت نفرستید."
    daily_cap, _hourly = get_effective_sender_limits(account)
    if (account.daily_action_count or 0) >= daily_cap:
        return f"سقف روزانهٔ این اکانت ({daily_cap} پیام) پر شده است."
    if not in_operating_window(now.astimezone(ZoneInfo("Asia/Tehran")).hour):
        return "بیرون از ساعت کاری ارسال است."
    return ""


def record_manual_dm(account, *, user_pk, text: str, actor: str = "") -> None:
    """Bookkeeping after an operator DM: account counters, the matching lead and the account timeline.

    Marking the lead keeps the outreach dispatcher from cold-DMing the same
    person again.
    """
    from apps.discovery.ig_state import log_event
    from apps.discovery.models import InstagramEngineLead

    now = timezone.now()
    account.daily_action_count = (account.daily_action_count or 0) + 1
    account.last_action_at = now
    account.save(update_fields=["daily_action_count", "last_action_at"])

    if user_pk:
        lead = InstagramEngineLead.objects.filter(tenant_id=account.tenant_id, ig_user_pk=str(user_pk)).first()
        if lead is not None and lead.status in ("NEW", "QUEUED", "FAILED", "SKIPPED"):
            lead.status = "DM_SENT"
            lead.sender_account = account.username
            lead.initial_dm_text = text
            lead.initial_dm_at = now
            history = list(lead.conversation_history or [])
            history.append({"sender": account.username, "direction": "out", "role": "manual_cockpit",
                            "text": text, "time": now.isoformat(), "delivery_status": "SENT"})
            lead.conversation_history = history
            lead.save(update_fields=["status", "sender_account", "initial_dm_text", "initial_dm_at",
                                     "conversation_history", "updated_at"])

    log_event(account, "manual_dm", "دایرکت دستی از کابین", {"user_pk": str(user_pk or "")}, actor)
