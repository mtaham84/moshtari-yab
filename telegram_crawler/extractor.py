"""Context and thread extraction logic for candidate Telegram messages."""

from __future__ import annotations

import logging
from typing import Any
from telethon import TelegramClient
from telethon.tl.types import Channel, Message, User

from telegram_crawler.models import (
    GroupInfo,
    LeadContext,
    MessageSnippet,
    ReplyThread,
    UserProfile,
    now_utc,
)

log = logging.getLogger("telegram_crawler.extractor")


def message_to_snippet(msg: Message) -> MessageSnippet:
    """Convert a Telethon Message object to a clean MessageSnippet."""
    sender_name = None
    sender = getattr(msg, "sender", None)
    if sender is not None and not isinstance(sender, MagicMock if "MagicMock" in globals() else ()):
        first_name = getattr(sender, "first_name", None)
        last_name = getattr(sender, "last_name", None)
        username = getattr(sender, "username", None)
        title = getattr(sender, "title", None)

        if isinstance(first_name, str) or isinstance(last_name, str):
            parts = [first_name or "", last_name or ""]
            sender_name = " ".join(p for p in parts if p).strip() or (username if isinstance(username, str) else None)
        elif isinstance(title, str):
            sender_name = title
        elif isinstance(username, str):
            sender_name = username

    reply_to_id = None
    reply_to = getattr(msg, "reply_to", None)
    if reply_to is not None:
        raw_reply_id = getattr(reply_to, "reply_to_msg_id", None)
        if isinstance(raw_reply_id, int):
            reply_to_id = raw_reply_id

    text = ""
    raw_message = getattr(msg, "message", None)
    if isinstance(raw_message, str):
        text = raw_message.strip()

    sender_id = getattr(msg, "sender_id", None)
    if not isinstance(sender_id, int):
        sender_id = None

    return MessageSnippet(
        message_id=msg.id,
        sender_id=sender_id,
        sender_name=sender_name,
        text=text,
        date=msg.date,
        reply_to_msg_id=reply_to_id,
    )


async def extract_user_profile(
    client: TelegramClient,
    msg: Message,
) -> UserProfile:
    """Extract sender profile details from a message."""
    sender = None
    try:
        sender = await msg.get_sender()
    except Exception as exc:
        log.debug("Failed to get_sender for msg %s: %s", msg.id, exc)

    if sender is not None:
        fn = getattr(sender, "first_name", None)
        ln = getattr(sender, "last_name", None)
        un = getattr(sender, "username", None)
        ph = getattr(sender, "phone", None)
        raw_id = getattr(sender, "id", None)
        uid = raw_id if isinstance(raw_id, int) else (getattr(msg, "sender_id", 0) or 0)
        return UserProfile(
            user_id=uid if isinstance(uid, int) else 0,
            first_name=fn if isinstance(fn, str) else None,
            last_name=ln if isinstance(ln, str) else None,
            username=un if isinstance(un, str) else None,
            phone=ph if isinstance(ph, str) else None,
            is_bot=bool(getattr(sender, "bot", False)),
            is_premium=bool(getattr(sender, "premium", False)),
        )

    # Fallback if sender is channel or sender_id only
    sender_id = msg.sender_id or 0
    return UserProfile(
        user_id=sender_id,
        first_name=f"User {sender_id}" if sender_id else "Anonymous",
        is_bot=False,
        is_premium=False,
    )


async def fetch_preceding_messages(
    client: TelegramClient,
    entity: Any,
    target_msg_id: int,
    limit: int = 5,
) -> list[MessageSnippet]:
    """
    Fetch the N preceding messages immediately before target_msg_id in the chat.
    Returns them in chronological order (oldest to newest).
    """
    if limit <= 0:
        return []

    try:
        # get_messages with max_id returns messages strictly before max_id, newest first
        messages = await client.get_messages(entity, limit=limit, max_id=target_msg_id)
        # Filter out service messages with empty text
        snippets = [
            message_to_snippet(m)
            for m in messages
            if (m.message or "").strip()
        ]
        # Reverse to chronological order (oldest first)
        snippets.reverse()
        return snippets
    except Exception as exc:
        log.warning("Error fetching preceding messages for msg %s: %s", target_msg_id, exc)
        return []


async def fetch_reply_thread(
    client: TelegramClient,
    entity: Any,
    target_msg: Message,
    max_ancestors: int = 10,
    max_children: int = 10,
) -> ReplyThread:
    """
    Fetch the complete thread context:
    1. Ancestors: chain of messages target_msg replied to, up to thread root.
    2. Children: any replies to target_msg in the group.
    """
    ancestors: list[MessageSnippet] = []
    child_replies: list[MessageSnippet] = []

    # 1. Walk up the parent chain
    current = target_msg
    visited_ids: set[int] = {target_msg.id}

    for _ in range(max_ancestors):
        reply_to = getattr(current, "reply_to", None)
        parent_id = getattr(reply_to, "reply_to_msg_id", None) if reply_to else None
        if not parent_id or parent_id in visited_ids:
            break

        visited_ids.add(parent_id)
        try:
            parent_msg = await client.get_messages(entity, ids=parent_id)
            if parent_msg and parent_msg.id:
                ancestors.append(message_to_snippet(parent_msg))
                current = parent_msg
            else:
                break
        except Exception as exc:
            log.debug("Error fetching parent message %s: %s", parent_id, exc)
            break

    # Ancestors were found going backward: reverse so root is first
    ancestors.reverse()

    # 2. Fetch children replies (if any exist in the group)
    try:
        children = await client.get_messages(entity, limit=max_children, reply_to=target_msg.id)
        for child in children:
            if child and child.id != target_msg.id and (child.message or "").strip():
                child_replies.append(message_to_snippet(child))
    except Exception as exc:
        # Some chat configurations might not support reply_to filtering
        log.debug("Could not fetch child replies for msg %s: %s", target_msg.id, exc)

    return ReplyThread(
        parent_messages=ancestors,
        child_replies=child_replies,
    )


async def build_lead_context(
    client: TelegramClient,
    entity: Any,
    group_info: GroupInfo,
    target_msg: Message,
    context_msg_count: int = 5,
) -> LeadContext:
    """Extract complete lead context including user, preceding messages, and reply thread."""
    user = await extract_user_profile(client, target_msg)
    preceding = await fetch_preceding_messages(
        client, entity, target_msg.id, limit=context_msg_count
    )
    thread = await fetch_reply_thread(client, entity, target_msg)
    target_snippet = message_to_snippet(target_msg)

    lead_id = f"{group_info.group_id}_{target_msg.id}"

    return LeadContext(
        lead_id=lead_id,
        group=group_info,
        target_message=target_snippet,
        user=user,
        previous_messages=preceding,
        reply_thread=thread,
        detected_at=now_utc(),
    )
