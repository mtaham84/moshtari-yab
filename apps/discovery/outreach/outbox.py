"""The queued outbox: manual DMs wait here and are sent by the outreach worker with a fitting sender."""
import logging
from typing import Dict, Any, Optional
from django.utils import timezone

from apps.discovery.outreach import journey

logger = logging.getLogger("instagram_outreach")


def enqueue_direct_message(
    lead_id: int,
    message_text: str,
    sender_username: Optional[str] = None,
    priority: str = "HIGH",
) -> Dict[str, Any]:
    """
    Enqueues an operator or AI direct message into the secure Dispatch Queue.
    Protects the sender account from burst rate violations and allows worker to
    smoothly dispatch with human spacing and organic navigation.
    """
    from apps.discovery.models import InstagramEngineLead, InstagramEngineAccount

    lead = InstagramEngineLead.objects.filter(pk=lead_id).first()
    if not lead:
        return {"success": False, "error": "مشتری مورد نظر یافت نشد."}

    sender = None
    if sender_username:
        sender = InstagramEngineAccount.objects.filter(username__iexact=sender_username, role="SENDER").first()
    if not sender and lead.second_sender_username:
        sender = InstagramEngineAccount.objects.filter(username__iexact=lead.second_sender_username, role="SENDER").first()
    if not sender and lead.sender_account:
        sender = InstagramEngineAccount.objects.filter(username__iexact=lead.sender_account, role="SENDER").first()
    if not sender:
        sender = InstagramEngineAccount.objects.filter(tenant_id=lead.tenant_id, role="SENDER", status="ACTIVE").first()

    chosen_sender = sender.username if sender else (sender_username or "مشاور")

    now = timezone.now()
    history = list(lead.conversation_history or [])
    history.append({
        "sender": chosen_sender,
        "direction": "out",
        "role": "operator_message",
        "text": message_text,
        "time": now.isoformat(),
        "delivery_status": "QUEUED",
        "priority": priority,
    })

    lead.status = "QUEUED"
    lead.conversation_history = history
    if sender:
        lead.second_sender_username = sender.username
    lead.save(update_fields=["status", "conversation_history", "second_sender_username", "updated_at"])

    logger.info("Message for @%s enqueued with priority %s for sender @%s", lead.username, priority, chosen_sender)
    return {
        "success": True,
        "queued": True,
        "lead": lead.username,
        "sender": chosen_sender,
        "message": f"پیام با موفقیت در صف ارسال امن قرار گرفت و به زودی توسط @{chosen_sender} ارسال می‌شود.",
    }


def resolve_queued_sender(lead: Any, queued_item: Optional[Dict[str, Any]] = None):
    """SENDER account that should deliver a queued message (chosen sender, else the lead's senders, else any ACTIVE)."""
    from apps.discovery.models import InstagramEngineAccount

    sender_username = (queued_item or {}).get("sender") or lead.second_sender_username or lead.sender_account
    sender = None
    if sender_username:
        sender = InstagramEngineAccount.objects.filter(username__iexact=sender_username, role="SENDER").first()
    if not sender:
        sender = InstagramEngineAccount.objects.filter(tenant_id=lead.tenant_id, role="SENDER", status="ACTIVE").first()
    return sender


def process_queued_outbox_message(
    lead: Any,
    client_cache: Optional[Dict[str, Any]] = None,
    dry_run: bool = False,
    sender: Any = None,
) -> Dict[str, Any]:
    """
    Safely dispatches the oldest pending QUEUED message for a lead using
    organic navigation and account session.
    """
    from apps.discovery.device_emulator import connect_saved_session
    from apps.discovery.ig_client import new_client

    history = list(getattr(lead, "conversation_history", []) or [])
    queued_idx = None
    queued_item = None
    for idx, item in enumerate(history):
        if item.get("direction") == "out" and item.get("delivery_status") == "QUEUED":
            queued_idx = idx
            queued_item = item
            break

    if queued_item is None:
        return {"success": False, "error": "هیچ پیام در صف برای این لید یافت نشد."}

    sender_username = queued_item.get("sender") or lead.second_sender_username or lead.sender_account
    if sender is None:
        sender = resolve_queued_sender(lead, queued_item)

    if not lead.has_replied:   # still a first contact: same rule as cold DMs (no link, @mention or phone number)
        from instagram_engine.analyzer import validate_and_sanitize_dm
        valid, reason = validate_and_sanitize_dm(queued_item.get("text") or "")
        if not valid:
            queued_item["delivery_status"] = "FAILED"
            queued_item["last_error"] = reason[:200]
            history[queued_idx] = queued_item
            lead.conversation_history = history
            lead.status = "DM_SENT" if lead.initial_dm_at else "FAILED"
            lead.save(update_fields=["status", "conversation_history", "updated_at"])
            return {"success": False, "blocked": True, "error": reason}

    if dry_run:
        now = timezone.now()
        chosen_sender = sender.username if sender else (sender_username or "مشاور")
        queued_item["delivery_status"] = "SENT"
        queued_item["sent_at"] = now.isoformat()
        history[queued_idx] = queued_item

        lead.conversation_history = history
        lead.status = "FOLLOWUP_SENT"
        if not lead.second_message_text:
            lead.second_message_text = queued_item["text"]
            lead.second_sender_username = chosen_sender
            lead.second_message_at = now
        lead.save(update_fields=["status", "conversation_history", "second_message_text", "second_sender_username", "second_message_at", "updated_at"])

        if sender:
            sender.daily_action_count = (sender.daily_action_count or 0) + 1
            sender.hourly_action_count = (sender.hourly_action_count or 0) + 1
            sender.last_action_at = now
            sender.save(update_fields=["daily_action_count", "hourly_action_count", "last_action_at"])

        return {
            "success": True,
            "dry_run": True,
            "lead": lead.username,
            "sender": chosen_sender,
            "text": queued_item["text"],
            "sent_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }

    if not sender or not sender.session_json:
        return {"success": False, "error": f"اکانت فرستنده معتبر برای @{lead.username} در دسترس نیست."}

    cookies = sender.session_json.get("cookies", {})
    sess_id = cookies.get("sessionid")
    if not sess_id:
        return {"success": False, "error": f"کوکی sessionid برای @{sender.username} ثبت نشده است."}

    cl = None
    if client_cache is not None and sender.username in client_cache:
        cl = client_cache[sender.username]
    else:
        cl = new_client(sender)
        connect_saved_session(cl, sender)
        if client_cache is not None:
            client_cache[sender.username] = cl

    try:
        # Execute in-app organic journey before dispatching DM
        target_pk = int(lead.ig_user_pk) if lead.ig_user_pk and str(lead.ig_user_pk).isdigit() else None
        journey_res = journey.execute_organic_journey(cl, lead.username, target_pk=target_pk)
        final_pk = journey_res.get("target_pk") or target_pk or int(cl.user_id_from_username(lead.username))

        # Send DM
        cl.direct_send(queued_item["text"], user_ids=[final_pk])

        now = timezone.now()
        queued_item["delivery_status"] = "SENT"
        queued_item["sent_at"] = now.isoformat()
        history[queued_idx] = queued_item

        lead.conversation_history = history
        lead.status = "FOLLOWUP_SENT"
        if not lead.second_message_text:
            lead.second_message_text = queued_item["text"]
            lead.second_sender_username = sender.username
            lead.second_message_at = now
        lead.save(update_fields=["status", "conversation_history", "second_message_text", "second_sender_username", "second_message_at", "updated_at"])

        sender.daily_action_count = (sender.daily_action_count or 0) + 1
        sender.hourly_action_count = (sender.hourly_action_count or 0) + 1
        sender.last_action_at = now
        sender.save(update_fields=["daily_action_count", "hourly_action_count", "last_action_at"])

        return {
            "success": True,
            "lead": lead.username,
            "sender": sender.username,
            "text": queued_item["text"],
            "sent_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as exc:
        logger.exception("Error dispatching queued message to @%s: %s", lead.username, exc)
        return {"success": False, "error": str(exc)}
