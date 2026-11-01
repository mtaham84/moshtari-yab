"""
Unified Sender Policy and Atomic Quota Reservation (P04.05 / R12).

Enforces:
1. Unified eligibility checks across role, status, cooldown, circadian hours,
   daily limits, and hourly rate limits.
2. Atomic quota reservation tied to intent before network transmission.
3. Idempotent reservation (retried intents do not consume double quota).
4. Symmetrical enforcement for initial outreach and follow-up messaging.
5. Admin limit overrides with security audit logging.
"""

from datetime import datetime, timedelta, timezone as dt_timezone
import logging
from typing import Tuple, Optional, Any

from django.core.cache import cache
from django.db import transaction
from django.db.models import F
from django.db.models.functions import Greatest
from django.utils import timezone

from apps.discovery.models import InstagramEngineAccount

try:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = dt_timezone(timedelta(hours=3, minutes=30))

logger = logging.getLogger("instagram_outreach")
audit_logger = logging.getLogger("security.audit")


class SenderPolicy:
    """
    Unified policy engine controlling account eligibility, pacing, and quota reservation.
    """

    @staticmethod
    def get_tehran_now(clock: Optional[datetime] = None) -> datetime:
        now = clock or timezone.now()
        return now.astimezone(TEHRAN_TZ)

    @classmethod
    def is_within_circadian_hours(cls, clock: Optional[datetime] = None) -> bool:
        """Enforces 09:00 - 23:00 Tehran operational window."""
        t_now = cls.get_tehran_now(clock)
        return 9 <= t_now.hour < 23

    @classmethod
    def check_eligibility(
        cls,
        sender: InstagramEngineAccount,
        clock: Optional[datetime] = None,
        ignore_circadian: bool = False,
    ) -> Tuple[bool, str]:
        """
        Unified eligibility evaluation. Returns (is_eligible, reason).
        Does NOT mutate database.
        """
        now = clock or timezone.now()
        t_now = cls.get_tehran_now(now)

        if sender.role != "SENDER":
            return False, f"اکانت @{sender.username} دارای نقش SENDER نیست."

        if sender.status != "ACTIVE":
            return False, f"اکانت @{sender.username} در وضعیت {sender.get_status_display()} قرار دارد."

        if sender.cooldown_until and sender.cooldown_until > now:
            cooldown_min = int((sender.cooldown_until - now).total_seconds() / 60)
            return False, f"اکانت @{sender.username} تا {cooldown_min} دقیقه دیگر در حالت استراحت (cooldown) است."

        if not ignore_circadian and not cls.is_within_circadian_hours(now):
            return False, f"ساعت محلی تهران ({t_now.strftime('%H:%M')}) خارج از بازه مجاز ارسال (۰۹:۰۰ تا ۲۳:۰۰) است."

        # Calculate effective in-memory counts
        eff_daily = sender.daily_action_count
        if sender.last_action_at and sender.last_action_at.astimezone(TEHRAN_TZ).date() < t_now.date():
            eff_daily = 0

        eff_hourly = sender.hourly_action_count or 0
        if sender.last_hour_reset and sender.last_hour_reset.astimezone(TEHRAN_TZ).hour != t_now.hour:
            eff_hourly = 0
        elif not sender.last_hour_reset:
            eff_hourly = 0

        limit_daily = sender.daily_limit or 18
        limit_hourly = sender.hourly_limit or 3

        if eff_daily >= limit_daily:
            return False, f"سقف ارسال روزانه اکانت @{sender.username} ({eff_daily}/{limit_daily}) تکمیل شده است."

        if eff_hourly >= limit_hourly:
            return False, f"سقف ارسال ساعتی اکانت @{sender.username} ({eff_hourly}/{limit_hourly}) برای این ساعت تکمیل شده است."

        return True, "ELIGIBLE"

    @classmethod
    def reserve_quota(
        cls,
        sender_id: int,
        intent_id: str,
        clock: Optional[datetime] = None,
        ignore_circadian: bool = False,
    ) -> Tuple[bool, str]:
        """
        Atomically reserves 1 unit of daily & hourly quota for an intent before transmission.
        Row locks InstagramEngineAccount via select_for_update().
        Idempotent: if intent_id already holds active reservation, succeeds without double-incrementing.
        """
        now = clock or timezone.now()
        t_now = cls.get_tehran_now(now)

        cache_key = f"sender_quota_reserved:{sender_id}:{intent_id}"
        if cache.get(cache_key):
            # Already reserved for this exact intent, idempotent bypass
            return True, "QUOTA_ALREADY_RESERVED"

        with transaction.atomic():
            try:
                account = InstagramEngineAccount.objects.select_for_update().get(pk=sender_id)
            except InstagramEngineAccount.DoesNotExist:
                return False, f"اکانت فرستنده شناسه {sender_id} یافت نشد."

            # Reset counts if expired before checking
            reset_fields = []
            if account.last_action_at and account.last_action_at.astimezone(TEHRAN_TZ).date() < t_now.date():
                account.daily_action_count = 0
                reset_fields.append("daily_action_count")

            if account.last_hour_reset and account.last_hour_reset.astimezone(TEHRAN_TZ).hour != t_now.hour:
                account.hourly_action_count = 0
                account.last_hour_reset = now
                reset_fields.extend(["hourly_action_count", "last_hour_reset"])
            elif not account.last_hour_reset:
                account.last_hour_reset = now
                reset_fields.append("last_hour_reset")

            if reset_fields:
                account.save(update_fields=list(set(reset_fields)))

            eligible, reason = cls.check_eligibility(account, clock=now, ignore_circadian=ignore_circadian)
            if not eligible:
                return False, reason

            # Atomic increment
            InstagramEngineAccount.objects.filter(pk=account.pk).update(
                daily_action_count=F("daily_action_count") + 1,
                hourly_action_count=F("hourly_action_count") + 1,
                last_action_at=now,
            )

            # Record reservation in cache for 1 hour timeout
            cache.set(cache_key, 1, timeout=3600)

        return True, "QUOTA_RESERVED"

    @classmethod
    def release_quota(
        cls,
        sender_id: int,
        intent_id: str,
    ) -> bool:
        """
        Compensatory release of reserved quota if an intent is cancelled or rejected
        before external network transmission starts.
        """
        cache_key = f"sender_quota_reserved:{sender_id}:{intent_id}"
        if not cache.get(cache_key):
            return False

        with transaction.atomic():
            InstagramEngineAccount.objects.filter(pk=sender_id).update(
                daily_action_count=Greatest(F("daily_action_count") - 1, 0),
                hourly_action_count=Greatest(F("hourly_action_count") - 1, 0),
            )
            cache.delete(cache_key)

        return True

    @classmethod
    def confirm_quota(
        cls,
        sender_id: int,
        intent_id: str,
    ) -> None:
        """
        Confirms quota consumption upon successful send or UNKNOWN (in-flight crash).
        """
        cache_key = f"sender_quota_reserved:{sender_id}:{intent_id}"
        cache.delete(cache_key)

    @classmethod
    def admin_override_quota(
        cls,
        sender_id: int,
        new_daily_limit: int,
        reason: str,
        admin_username: str,
    ) -> None:
        """
        Allows an administrator to override an account limit with mandatory audit logging.
        """
        if not reason or len(reason.strip()) < 5:
            raise ValueError("درج دلیل موجه با حداقل ۵ کاراکتر برای تغییر سهمیه الزامی است.")

        with transaction.atomic():
            account = InstagramEngineAccount.objects.select_for_update().get(pk=sender_id)
            old_limit = account.daily_limit
            account.daily_limit = new_daily_limit
            account.save(update_fields=["daily_limit"])

            audit_logger.warning(
                "SENDER_QUOTA_ADMIN_OVERRIDE: Account @%s (id=%s) daily_limit changed from %s to %s by admin '%s'. Reason: %s",
                account.username,
                account.id,
                old_limit,
                new_daily_limit,
                admin_username,
                reason,
            )
