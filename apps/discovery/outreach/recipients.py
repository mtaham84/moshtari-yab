"""Choosing the next lead to message (atomic reservation) and checking the recipient's profile first."""
import logging
from datetime import timedelta
from typing import Dict, Any, List, Optional
from django.utils import timezone

logger = logging.getLogger("instagram_outreach")


def select_and_reserve_candidate(tenant_ids: List[str], *, now=None) -> Optional[Any]:
    """
    Gold standard distributed locking:
    Atomically selects and locks candidate NEW leads using select_for_update(skip_locked=True),
    updates scoring, skips ineligible ones, and marks the highest-scoring candidate as 'PROCESSING'.
    Ensures parallel workers never double-message leads or encounter race conditions.
    """
    from django.db import transaction
    from django.db.models import Q
    from apps.discovery.models import InstagramEngineLead
    from apps.discovery.instagram_scoring import update_lead_scoring, get_outreach_cutoff_days

    now = now or timezone.now()
    cutoff_days = get_outreach_cutoff_days()
    cutoff = now - timedelta(days=cutoff_days)

    with transaction.atomic():
        candidates = list(
            InstagramEngineLead.objects.select_for_update(skip_locked=True)
            .filter(tenant_id__in=tenant_ids, status="NEW")
            .filter(Q(comment_created_at__gte=cutoff) | Q(comment_created_at__isnull=True, created_at__gte=cutoff))
            .order_by("-outreach_score", "-comment_created_at", "-created_at")[:15]
        )
        if not candidates:
            return None

        scored = []
        for cand in candidates:
            calc = update_lead_scoring(cand, commit=False, now=now)
            cand.outreach_score = calc.get("total_score", cand.outreach_score)
            if calc.get("is_eligible", True):
                scored.append(cand)
            else:
                cand.save(update_fields=["outreach_score", "updated_at"])

        if not scored:
            return None

        # Pick the highest-scored candidate
        scored.sort(
            key=lambda l: (
                l.outreach_score,
                l.comment_created_at or l.created_at,
            ),
            reverse=True,
        )
        winner = scored[0]
        winner.status = "PROCESSING"
        winner.save(update_fields=["status", "outreach_score", "updated_at"])
        return winner


def qualify_recipient_profile(cl: Any, username: str, target_pk: Optional[int] = None) -> Dict[str, Any]:
    """
    Super Pre-flight safety check on recipient before sending DM:
    Uses mobile private API only (never falls back to slow/blocked public web scraping).
    1. Detects Private Accounts: Sending cold DMs to private accounts triggers Instagram error 1404006.
    2. Detects Ghost/Fake Accounts: Accounts with 0 posts & 0 followers risk spam flagging.
    Returns:
        {"eligible": True/False, "reason": str, "user_info": Any, "target_pk": int, "is_private": bool}
    """
    user_data = None
    numeric_pk = int(target_pk) if target_pk and str(target_pk).isdigit() else None

    # 1. Try mobile private user info by numeric PK
    if numeric_pk:
        try:
            res = cl.private_request(f"users/{numeric_pk}/info/", params={"entry_point": "profile", "from_module": "profile"})
            if isinstance(res, dict) and res.get("status") == "ok" and "user" in res:
                user_data = res["user"]
        except Exception as pk_err:
            logger.debug(f"Private user info by PK {numeric_pk} failed: {pk_err}", exc_info=True)

    # 2. If no data, try private usernameinfo
    if not user_data and username:
        try:
            res = cl.private_request(f"users/{username}/usernameinfo/")
            if isinstance(res, dict) and res.get("status") == "ok" and "user" in res:
                user_data = res["user"]
        except Exception as un_err:
            logger.debug(f"Private usernameinfo for {username} failed: {un_err}", exc_info=True)

    if user_data:
        is_private = bool(user_data.get("is_private", False))
        resolved_pk = int(user_data.get("pk") or numeric_pk or 0)
        media_count = int(user_data.get("media_count", 1))
        follower_count = int(user_data.get("follower_count", 1))

        if media_count == 0 and follower_count == 0:
            return {
                "eligible": False,
                "is_private": is_private,
                "reason": "پروفایل غیرفعال یا مشکوک (۰ پست و ۰ فالوور) جهت ایمنی رد شد.",
                "user_info": user_data,
                "target_pk": resolved_pk,
            }

        # Private accounts can safely receive direct messages (delivered into Message Requests / درخواست پیام)
        # We allow them so outreach does not stall when prospective customers have private personal profiles.
        return {
            "eligible": True,
            "is_private": is_private,
            "reason": "واجد شرایط دریافت دایرکت" if not is_private else "واجد شرایط دریافت دایرکت (پیج خصوصی - ارسال به صندوق درخواست پیام)",
            "user_info": user_data,
            "target_pk": resolved_pk,
        }

    # If user lookup returned fail / not found, but we already have a verified numeric PK from comment
    if numeric_pk:
        return {
            "eligible": True,
            "reason": "واجد شرایط دریافت دایرکت بر اساس PK معتبر کاربری",
            "user_info": None,
            "target_pk": numeric_pk,
        }

    return {
        "eligible": False,
        "reason": "عدم امکان احراز هویت شناسه کاربر در اینستاگرام",
        "user_info": None,
        "target_pk": None,
    }
