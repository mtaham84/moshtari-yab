"""Conversation threads and syncing incoming replies from Instagram."""
from datetime import timedelta
import logging
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.discovery.outreach.messages import extract_iranian_phone_number

logger = logging.getLogger("instagram_outreach")
TEHRAN_TZ = ZoneInfo("Asia/Tehran")


def _reply_sync_spacing() -> timedelta:
    return timedelta(seconds=max(60, int(getattr(settings, "INSTAGRAM_REPLY_SYNC_MIN_SECONDS", 300))))


def _next_shift_label(now, shift_reason: str, wait_seconds: int) -> tuple[str, str]:
    if wait_seconds and wait_seconds > 0:
        next_tehran = (now + timedelta(seconds=wait_seconds)).astimezone(TEHRAN_TZ).strftime("%H:%M")
        return next_tehran, f"حدود ساعت {next_tehran}"
    return "", shift_reason or "شروع شیفت بعدی"


def request_reply_sync(tenant_id: str, *, now=None) -> Dict[str, Any]:
    """Queue an inbox reply check for the outreach dispatcher without hitting Instagram from the web process."""
    from apps.discovery.models import OutreachTenantProfile
    from apps.discovery.outreach_engine import reply_sync_window
    from apps.discovery.tenants import canonical_tenant

    now = now or timezone.now()
    tid = canonical_tenant(tenant_id)
    spacing = _reply_sync_spacing()
    allowed, shift_reason, wait_seconds = reply_sync_window()

    OutreachTenantProfile.objects.get_or_create(tenant_id=tid)
    with transaction.atomic():
        profile = OutreachTenantProfile.objects.select_for_update().get(tenant_id=tid)
        if profile.last_reply_sync_at and (now - profile.last_reply_sync_at) < spacing:
            remaining = max(1, int((spacing - (now - profile.last_reply_sync_at)).total_seconds()))
            next_tehran = (profile.last_reply_sync_at + spacing).astimezone(TEHRAN_TZ).strftime("%H:%M")
            mins = max(1, (remaining + 59) // 60)
            return {
                "status": "throttled",
                "queued": False,
                "allowed": allowed,
                "wait_seconds": remaining,
                "next_run_at": next_tehran,
                "message": f"صندوق پیام‌ها به‌تازگی بررسی شده است؛ بررسی بعدی حدود {mins} دقیقه دیگر (ساعت {next_tehran}) مجاز است.",
            }

        already_queued = profile.reply_sync_requested_at is not None
        if not already_queued:
            profile.reply_sync_requested_at = now
            profile.save(update_fields=["reply_sync_requested_at", "updated_at"])

    if not allowed:
        next_tehran, when_label = _next_shift_label(now, shift_reason, wait_seconds)
        prefix = "درخواست بررسی صندوق از قبل ثبت شده است" if already_queued else "درخواست بررسی صندوق ثبت شد"
        return {
            "status": "outside_shift",
            "queued": True,
            "allowed": False,
            "wait_seconds": wait_seconds,
            "next_run_at": next_tehran,
            "reason": shift_reason,
            "message": (
                f"{prefix}؛ ربات خارج از ساعت کاری است و "
                f"بررسی در شروع شیفت بعدی ({when_label} — {shift_reason}) انجام می‌شود."
            ),
        }

    if already_queued:
        return {
            "status": "already_queued",
            "queued": True,
            "allowed": True,
            "wait_seconds": 0,
            "next_run_at": "",
            "message": "درخواست بررسی صندوق از قبل در صف ربات پیام‌رسان است و در چرخه جاری انجام می‌شود.",
        }

    return {
        "status": "queued",
        "queued": True,
        "allowed": True,
        "wait_seconds": 0,
        "next_run_at": "",
        "message": "درخواست بررسی صندوق پیام‌ها برای ربات پیام‌رسان ثبت شد و در چرخه جاری (با رعایت فاصله ایمنی) انجام می‌شود.",
    }


def has_pending_reply_sync_requests(tenant_ids: List[str]) -> bool:
    """True when any canonical tenant in ``tenant_ids`` has an unserved panel reply-sync request."""
    from apps.discovery.models import OutreachTenantProfile

    if not tenant_ids:
        return False
    return OutreachTenantProfile.objects.filter(
        tenant_id__in=tenant_ids,
        reply_sync_requested_at__isnull=False,
    ).exists()


def serve_reply_syncs(
    tenant_groups: List[List[str]],
    *,
    requested_only: bool = False,
    ignore_shift: bool = False,
    sync_fn: Optional[Callable[[List[str]], Dict[str, Any]]] = None,
    now=None,
) -> List[Dict[str, Any]]:
    """Run reply sync for due tenant groups inside shift hours and per-tenant spacing."""
    from apps.discovery.models import OutreachTenantProfile
    from apps.discovery.outreach_engine import reply_sync_window
    from apps.discovery.tenants import canonical_tenant

    if not reply_sync_window(ignore_shift=ignore_shift)[0]:
        return []

    now = now or timezone.now()
    spacing = _reply_sync_spacing()
    runner = sync_fn or check_and_sync_lead_replies
    results: List[Dict[str, Any]] = []

    for group in tenant_groups:
        if not group:
            continue
        tid = canonical_tenant(group[0])
        OutreachTenantProfile.objects.get_or_create(tenant_id=tid)
        with transaction.atomic():
            profile = OutreachTenantProfile.objects.select_for_update().get(tenant_id=tid)
            should_run = True
            if requested_only and profile.reply_sync_requested_at is None:
                should_run = False
            elif profile.last_reply_sync_at and (now - profile.last_reply_sync_at) < spacing:
                should_run = False

            if should_run:
                profile.last_reply_sync_at = now
                profile.reply_sync_requested_at = None
                profile.save(update_fields=["last_reply_sync_at", "reply_sync_requested_at", "updated_at"])

        if should_run:
            results.append(runner(group))

    return results


def get_lead_conversation_thread(lead: Any) -> List[Dict[str, Any]]:
    """
    Returns the complete, chronologically sorted list of direct messages for an Instagram lead.
    Gracefully reconstructs missing entries from legacy fields (initial_dm_text, reply_text, second_message_text).
    """
    history = list(getattr(lead, "conversation_history", []) or [])

    # If history is empty, reconstruct from legacy single-message fields
    if not history:
        if getattr(lead, "initial_dm_text", None):
            history.append({
                "sender": getattr(lead, "sender_account", "") or "مشاور",
                "direction": "out",
                "role": "initial_outreach",
                "text": lead.initial_dm_text,
                "time": lead.initial_dm_at.isoformat() if getattr(lead, "initial_dm_at", None) else "",
            })
        if getattr(lead, "reply_text", None):
            history.append({
                "sender": getattr(lead, "username", "") or "مشتری",
                "direction": "in",
                "role": "lead_reply",
                "text": lead.reply_text,
                "time": lead.reply_at.isoformat() if getattr(lead, "reply_at", None) else "",
            })
        if getattr(lead, "second_message_text", None):
            history.append({
                "sender": getattr(lead, "second_sender_username", "") or getattr(lead, "sender_account", "") or "مشاور",
                "direction": "out",
                "role": "followup",
                "text": lead.second_message_text,
                "time": lead.second_message_at.isoformat() if getattr(lead, "second_message_at", None) else "",
            })
    else:
        # Check if initial_dm_text is missing from the beginning of history
        first_msg = history[0] if history else {}
        if getattr(lead, "initial_dm_text", None) and first_msg.get("direction") == "in":
            history.insert(0, {
                "sender": getattr(lead, "sender_account", "") or "مشاور",
                "direction": "out",
                "role": "initial_outreach",
                "text": lead.initial_dm_text,
                "time": lead.initial_dm_at.isoformat() if getattr(lead, "initial_dm_at", None) else "",
            })

        # Check if second_message_text was sent but not added to history
        if getattr(lead, "second_message_text", None):
            has_second = any(
                m.get("direction") == "out" and m.get("text") == lead.second_message_text
                for m in history
            )
            if not has_second:
                history.append({
                    "sender": getattr(lead, "second_sender_username", "") or getattr(lead, "sender_account", "") or "مشاور",
                    "direction": "out",
                    "role": "followup",
                    "text": lead.second_message_text,
                    "time": lead.second_message_at.isoformat() if getattr(lead, "second_message_at", None) else "",
                })

    formatted_thread = []
    for m in history:
        direction = m.get("direction", "out")
        sender = m.get("sender", "")
        time_str = m.get("time", "")
        formatted_thread.append({
            "sender": sender,
            "direction": direction,
            "role": m.get("role", "message"),
            "text": m.get("text", ""),
            "time": time_str,
            "delivery_status": m.get("delivery_status", "SENT"),
            "is_incoming": direction == "in",
            "is_queued": m.get("delivery_status") == "QUEUED",
        })
    return formatted_thread


def check_and_sync_lead_replies(tenant_ids: List[str]) -> Dict[str, Any]:
    """
    Checks direct message inboxes of all SENDER accounts for given tenants.
    Detects if any lead has replied, saves their responses to conversation_history,
    extracts Iranian mobile numbers, and marks lead as REPLIED.
    """
    from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead, Customer

    if isinstance(tenant_ids, str):
        tenant_ids = [tenant_ids]

    senders = InstagramEngineAccount.objects.filter(
        role="SENDER",
        status="ACTIVE",
        tenant_id__in=tenant_ids,
    )

    if not senders.exists():
        return {"status": "no_senders", "replies_found": 0, "details": []}

    synced_count = 0
    details = []

    for sender in senders:
        if not sender.session_json:
            continue

        cookies = sender.session_json.get("cookies", {})
        sessionid = cookies.get("sessionid")
        if not sessionid:
            continue

        try:
            # Reuse the dispatcher's logged-in client instead of a fresh login on every poll
            from apps.discovery.outreach_engine import get_sender_client
            cl = get_sender_client(sender)
            current_user_id = str(cl.user_id)

            # Fast single chunk fetch (max 10 recent threads without paging loop)
            threads, _ = cl.direct_threads_chunk(thread_message_limit=10)
            for th in threads[:10]:
                messages = getattr(th, "messages", [])
                if not messages:
                    continue

                # Find messages in this thread from the customer
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

                    history = list(lead.conversation_history or [])
                    new_msgs_added = False

                    # Process customer messages in chronological order (oldest to newest)
                    for cm in reversed(customer_messages):
                        cm_text = str(getattr(cm, "text", "") or "").strip()
                        if not cm_text:
                            continue
                        cm_time = getattr(cm, "timestamp", None) or msg_time
                        cm_time_iso = cm_time.isoformat() if hasattr(cm_time, "isoformat") else str(cm_time)

                        already_in_history = any(
                            m.get("direction") == "in" and m.get("text") == cm_text
                            for m in history
                        )
                        if not already_in_history:
                            history.append({
                                "sender": lead.username,
                                "direction": "in",
                                "text": cm_text,
                                "time": cm_time_iso,
                            })
                            new_msgs_added = True

                            # Check for Iranian phone number
                            phone = extract_iranian_phone_number(cm_text)
                            if phone and lead.ig_user_pk:
                                Customer.objects.filter(
                                    external_user_id=str(lead.ig_user_pk)
                                ).update(phone_number=phone)

                    if not lead.has_replied or lead.reply_text != msg_text or new_msgs_added:
                        lead.has_replied = True
                        lead.status = "REPLIED"
                        lead.reply_text = msg_text
                        lead.reply_at = msg_time
                        lead.sender_account = lead.sender_account or sender.username
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
            logger.warning(f"Error checking replies on sender @{sender.username}: {exc}", exc_info=True)

    return {
        "status": "success",
        "replies_found": synced_count,
        "details": details,
    }
