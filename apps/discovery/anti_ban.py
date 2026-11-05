"""Instagram Anti-Ban & Natural Human Behavior Manager.

Provides centralized enforcement of:
1. Operational Time Window: Only 09:00 to 23:00 Tehran time.
2. Daily Quotas: Max API requests (120), max search queries (20), max posts inspected (45).
3. Anti-Fingerprint Human Delays: Randomized jitter between queries (35-65s), posts (8-16s), comments (3-6s).
4. Random Human Breaks: Periodic natural rest breaks (2.5 - 6.5 minutes) every 3-6 actions.
5. Modular Onboarding: Automatically assigns distinct device emulation & isolated proxy to any account.
"""
from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timedelta
from typing import Tuple

from django.db.models import Sum
from django.utils import timezone as dj_timezone

log = logging.getLogger("instagram.antiban")

# Operating hours in Asia/Tehran
INSTAGRAM_WINDOW_START_HOUR = int(os.getenv("INSTAGRAM_WINDOW_START_HOUR", "9"))   # 09:00
INSTAGRAM_WINDOW_END_HOUR = int(os.getenv("INSTAGRAM_WINDOW_END_HOUR", "23"))     # 23:00

# Daily safety quotas per scraper account
INSTAGRAM_MAX_DAILY_REQUESTS = int(os.getenv("INSTAGRAM_MAX_DAILY_REQUESTS", "350"))
INSTAGRAM_MAX_DAILY_QUERIES = int(os.getenv("INSTAGRAM_MAX_DAILY_QUERIES", "60"))
INSTAGRAM_MAX_DAILY_POSTS = int(os.getenv("INSTAGRAM_MAX_DAILY_POSTS", "150"))
INSTAGRAM_MAX_COMMENTS_PER_POST = int(os.getenv("INSTAGRAM_MAX_COMMENTS_PER_POST", "50"))

# Delays (seconds) with randomized jitter
DELAY_BETWEEN_QUERIES = (35.0, 65.0)
DELAY_BETWEEN_POSTS = (8.0, 16.0)
DELAY_BETWEEN_COMMENTS = (3.0, 6.0)
DELAY_BETWEEN_BUSINESSES = (15.0, 30.0)

# Random human break configuration
BREAK_ACTION_THRESHOLD_MIN = 3
BREAK_ACTION_THRESHOLD_MAX = 6
BREAK_DURATION_MIN_SEC = 150.0   # 2.5 minutes
BREAK_DURATION_MAX_SEC = 390.0   # 6.5 minutes


def _antiban_disabled() -> bool:
    """settings.INSTAGRAM_ANTIBAN_ENABLED=False (test runner / conftest) skips the hours window and human pauses."""
    from django.conf import settings

    return not getattr(settings, "INSTAGRAM_ANTIBAN_ENABLED", True)


def in_operating_window(hour: int) -> bool:
    """True when a local (Tehran) hour is inside INSTAGRAM_WINDOW_START_HOUR..INSTAGRAM_WINDOW_END_HOUR."""
    return INSTAGRAM_WINDOW_START_HOUR <= hour < INSTAGRAM_WINDOW_END_HOUR


def next_window_start(local_now: datetime) -> datetime:
    """The next moment the operating window opens, in local_now's timezone."""
    start = local_now.replace(hour=INSTAGRAM_WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    return start if local_now.hour < INSTAGRAM_WINDOW_START_HOUR else start + timedelta(days=1)


class InstagramAntiBanManager:
    """Centralized manager for Instagram anti-ban rules and human behavior simulation."""

    _session_action_counts: dict[int, int] = {}
    _next_break_thresholds: dict[int, int] = {}

    @classmethod
    def is_within_operating_hours(cls, force: bool = False) -> bool:
        """Returns True if current local time (Asia/Tehran) is between 09:00 and 23:00."""
        if force or _antiban_disabled():
            return True
        return in_operating_window(dj_timezone.localtime().hour)

    @classmethod
    def seconds_until_next_window(cls) -> float:
        """Calculates seconds until the next 09:00 AM window begins in local time."""
        local_now = dj_timezone.localtime()
        diff = (next_window_start(local_now) - local_now).total_seconds()
        return max(60.0, diff)

    @classmethod
    def validate_account_role(cls, account, required_role: str) -> Tuple[bool, str]:
        """
        Guarantees strict separation between SCRAPER and SENDER accounts.
        Never allows a SENDER account to be used for scraping, nor a SCRAPER for DMs.
        """
        role = getattr(account, "role", "SCRAPER")
        if role != required_role:
            return False, f"تداخل نقش: اکانت @{getattr(account, 'username', '')} دارای نقش {role} است و برای {required_role} مجاز نیست."
        return True, "OK"

    @classmethod
    def get_account_daily_usage(cls, account) -> dict[str, int]:
        """Calculates the account's total requests, queries, and posts inspected today."""
        acc_id = getattr(account, "id", None)
        if not acc_id:
            return {"requests": 0, "posts_inspected": 0, "queries_executed": 0}

        from apps.discovery.models import InstagramCrawlExecution

        today = dj_timezone.localdate()
        qs = InstagramCrawlExecution.objects.filter(account=account, crawled_at__date=today)
        agg = qs.aggregate(
            total_requests=Sum("requests"),
            total_posts=Sum("posts_inspected"),
        )
        return {
            "requests": int(agg["total_requests"] or 0),
            "posts_inspected": int(agg["total_posts"] or 0),
            "queries_executed": qs.count(),
        }

    @classmethod
    def check_account_safety(cls, account, extra_requests: int = 0) -> Tuple[bool, str]:
        """
        Validates whether the account is within all safety limits:
        - Status is ACTIVE
        - Cooldown expired
        - Requests < 120/day
        - Queries < 20/day
        - Posts inspected < 45/day
        """
        acc_id = getattr(account, "id", None)
        if not acc_id:
            return True, "OK"

        status = getattr(account, "status", "ACTIVE")
        if status != "ACTIVE":
            return False, f"وضعیت اکانت {status} است"

        now = dj_timezone.now()
        cooldown = getattr(account, "cooldown_until", None)
        if cooldown and cooldown > now:
            return False, f"اکانت در حالت استراحت است (تا {cooldown.strftime('%H:%M')})"

        usage = cls.get_account_daily_usage(account)
        if (usage["requests"] + extra_requests) >= INSTAGRAM_MAX_DAILY_REQUESTS:
            return False, f"سقف مجاز درخواست روزانه تکمیل شد ({usage['requests']}/{INSTAGRAM_MAX_DAILY_REQUESTS})"

        if usage["queries_executed"] >= INSTAGRAM_MAX_DAILY_QUERIES:
            return False, f"سقف مجاز کوئری‌های روزانه تکمیل شد ({usage['queries_executed']}/{INSTAGRAM_MAX_DAILY_QUERIES})"

        if usage["posts_inspected"] >= INSTAGRAM_MAX_DAILY_POSTS:
            return False, f"سقف مجاز بررسی پست‌های روزانه تکمیل شد ({usage['posts_inspected']}/{INSTAGRAM_MAX_DAILY_POSTS})"

        return True, "OK"

    @classmethod
    def record_query_action(cls, account, stdout=None) -> None:
        """
        Records a completed query action and checks if a random human break is due.
        Real users take periodic breaks while browsing Instagram.
        """
        acc_id = getattr(account, "id", None)
        if not acc_id or _antiban_disabled():
            return

        cls._session_action_counts[acc_id] = cls._session_action_counts.get(acc_id, 0) + 1
        current = cls._session_action_counts[acc_id]

        threshold = cls._next_break_thresholds.get(acc_id)
        if not threshold:
            threshold = random.randint(BREAK_ACTION_THRESHOLD_MIN, BREAK_ACTION_THRESHOLD_MAX)
            cls._next_break_thresholds[acc_id] = threshold

        if current >= threshold:
            break_seconds = random.uniform(BREAK_DURATION_MIN_SEC, BREAK_DURATION_MAX_SEC)
            break_minutes = break_seconds / 60.0
            msg = (
                f"⏸️ [استراحت تصادفی انسانی]: اکانت @{account.username} پس از {current} جستجو "
                f"به مدت {break_minutes:.1f} دقیقه وارد استراحت تصادفی می‌شود تا رفتار کاملاً طبیعی حفظ شود."
            )
            if stdout:
                stdout.write(msg)
            else:
                log.info(msg)

            time.sleep(break_seconds)
            # Reset counter and pick a fresh random threshold
            cls._session_action_counts[acc_id] = 0
            cls._next_break_thresholds[acc_id] = random.randint(BREAK_ACTION_THRESHOLD_MIN, BREAK_ACTION_THRESHOLD_MAX)

    @classmethod
    def sleep_after_query(cls, stdout=None) -> None:
        """Sleep with random jitter between queries (35-65s)."""
        if _antiban_disabled():
            return
        duration = random.uniform(*DELAY_BETWEEN_QUERIES)
        if stdout:
            stdout.write(f"  ⏳ تاخیر طبیعی قبل از کوئری بعدی: {duration:.1f} ثانیه")
        time.sleep(duration)

    @classmethod
    def sleep_after_post(cls, stdout=None) -> None:
        """Sleep with random jitter between inspecting posts (8-16s)."""
        if _antiban_disabled():
            return
        duration = random.uniform(*DELAY_BETWEEN_POSTS)
        time.sleep(duration)

    @classmethod
    def sleep_after_comment_fetch(cls) -> None:
        """Sleep with random jitter after fetching comments from API (3-6s)."""
        if _antiban_disabled():
            return
        duration = random.uniform(*DELAY_BETWEEN_COMMENTS)
        time.sleep(duration)

    @classmethod
    def sleep_after_business(cls, stdout=None) -> None:
        """Sleep with random jitter between business targets (15-30s)."""
        if _antiban_disabled():
            return
        duration = random.uniform(*DELAY_BETWEEN_BUSINESSES)
        if stdout:
            stdout.write(f"  ⏳ تاخیر جابجایی بین کسب‌وکارها: {duration:.1f} ثانیه")
        time.sleep(duration)

    @classmethod
    def ensure_account_isolated_profile(cls, account) -> bool:
        """
        Ensures any account (current or future) has a fully distinct hardware device
        profile and a valid proxy configured.
        """
        from apps.discovery.device_emulator import ensure_account_device_profile

        changed = False
        if not account.device_settings or not account.device_settings.get("uuids"):
            ensure_account_device_profile(account)
            changed = True

        if not account.proxy_url and not account.proxy_config_id and account.pk:
            # No proxy of its own: a free system config of its own (it stays with the account), else the shared
            # pool (account.clean_proxy). It used to get a hard-coded xray port that may not exist at all.
            from apps.discovery.ig_accounts import _heal_account
            changed = bool(_heal_account(account, "system")) or changed

        return changed
