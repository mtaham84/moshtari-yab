"""Conversation threads and syncing incoming replies from Instagram."""
import logging
from typing import Dict, Any, List
from django.utils import timezone

from apps.discovery.outreach.messages import extract_iranian_phone_number

logger = logging.getLogger("instagram_outreach")


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
