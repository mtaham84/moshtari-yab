"""Context and thread extraction for candidate Telegram messages.

Context is built from the local message archive first (messages table) and only
falls back to the Telegram API when something is missing. Every API call goes
through flood-wait handling and pacing.
"""

from __future__ import annotations

import logging
from typing import Any

from telethon import TelegramClient
from telethon.tl.types import Message

from telegram_crawler import db as local_db
from telegram_crawler.models import (
    GroupInfo,
    LeadContext,
    MessageSnippet,
    ReplyThread,
    UserProfile,
    now_utc,
)
from telegram_crawler.ratelimit import pace, with_flood_retry

log = logging.getLogger("telegram_crawler.extractor")


def sender_display_name(sender: Any) -> str | None:
    if sender is None:
        return None
    first_name = getattr(sender, "first_name", None)
    last_name = getattr(sender, "last_name", None)
    username = getattr(sender, "username", None)
    title = getattr(sender, "title", None)
    if isinstance(first_name, str) or isinstance(last_name, str):
        parts = [p for p in (first_name, last_name) if isinstance(p, str) and p]
        return " ".join(parts).strip() or (username if isinstance(username, str) else None)
    if isinstance(title, str):
        return title
    if isinstance(username, str):
        return username
    return None


def message_to_snippet(msg: Message) -> MessageSnippet:
    """Convert a Telethon Message object to a clean MessageSnippet."""
    reply_to_id = None
    reply_to = getattr(msg, "reply_to", None)
    if reply_to is not None:
        raw_reply_id = getattr(reply_to, "reply_to_msg_id", None)
        if isinstance(raw_reply_id, int):
            reply_to_id = raw_reply_id

    raw_message = getattr(msg, "message", None)
    text = raw_message.strip() if isinstance(raw_message, str) else ""

    sender_id = getattr(msg, "sender_id", None)
    if not isinstance(sender_id, int):
        sender_id = None

    return MessageSnippet(
        message_id=msg.id,
        sender_id=sender_id,
        sender_name=sender_display_name(getattr(msg, "sender", None)),
        text=text,
        date=msg.date,
        reply_to_msg_id=reply_to_id,
    )


async def extract_user_profile(client: TelegramClient, msg: Message) -> UserProfile:
    """Extract sender profile details (no phone numbers are collected)."""
    sender = getattr(msg, "sender", None)
    if sender is None:
        try:
            sender = await with_flood_retry(lambda: msg.get_sender(), what="get_sender")
        except Exception as exc:
            log.debug("Failed to get_sender for msg %s: %s", msg.id, exc)

    if sender is not None:
        fn = getattr(sender, "first_name", None)
        ln = getattr(sender, "last_name", None)
        un = getattr(sender, "username", None)
        raw_id = getattr(sender, "id", None)
        uid = raw_id if isinstance(raw_id, int) else (getattr(msg, "sender_id", 0) or 0)
        return UserProfile(
            user_id=uid if isinstance(uid, int) else 0,
            first_name=fn if isinstance(fn, str) else (getattr(sender, "title", None) if isinstance(getattr(sender, "title", None), str) else None),
            last_name=ln if isinstance(ln, str) else None,
            username=un if isinstance(un, str) else None,
            is_bot=getattr(sender, "bot", False) is True,
            is_premium=getattr(sender, "premium", False) is True,
        )

    sender_id = msg.sender_id if isinstance(getattr(msg, "sender_id", None), int) else 0
    return UserProfile(
        user_id=sender_id,
        first_name=f"User {sender_id}" if sender_id else "Anonymous",
    )


async def fetch_preceding_messages(
    client: TelegramClient,
    entity: Any,
    target_msg_id: int,
    limit: int = 5,
    group_id: int | None = None,
    db_path: str | None = None,
) -> list[MessageSnippet]:
    """N messages right before the target, oldest first. Local archive first, then Telegram."""
    if limit <= 0:
        return []
    if group_id is not None:
        local = local_db.get_local_previous(group_id, target_msg_id, limit, db_path=db_path)
        if len(local) >= limit or (local and local[0].message_id <= 1):
            return local[-limit:]
    try:
        messages = await with_flood_retry(
            lambda: client.get_messages(entity, limit=limit, max_id=target_msg_id),
            what="get_messages(previous)",
        )
        await pace()
        snippets = [message_to_snippet(m) for m in messages if (getattr(m, "message", None) or "").strip()]
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
    group_id: int | None = None,
    db_path: str | None = None,
    fetch_children_remote: bool = True,
) -> ReplyThread:
    """Ancestors (reply chain up to the root, root first) and direct replies to the target."""
    ancestors: list[MessageSnippet] = []
    child_replies: list[MessageSnippet] = []

    current_parent_id = getattr(getattr(target_msg, "reply_to", None), "reply_to_msg_id", None)
    visited_ids: set[int] = {target_msg.id}

    for _ in range(max_ancestors):
        if not isinstance(current_parent_id, int) or current_parent_id in visited_ids:
            break
        visited_ids.add(current_parent_id)
        parent: MessageSnippet | None = None
        if group_id is not None:
            parent = local_db.get_local_message(group_id, current_parent_id, db_path=db_path)
        if parent is None:
            try:
                parent_msg = await with_flood_retry(
                    lambda pid=current_parent_id: client.get_messages(entity, ids=pid),
                    what="get_messages(parent)",
                )
                await pace()
                if parent_msg and getattr(parent_msg, "id", None):
                    parent = message_to_snippet(parent_msg)
            except Exception as exc:
                log.debug("Error fetching parent message %s: %s", current_parent_id, exc)
        if parent is None:
            break
        ancestors.append(parent)
        current_parent_id = parent.reply_to_msg_id

    ancestors.reverse()

    if group_id is not None:
        child_replies = local_db.get_local_replies(group_id, target_msg.id, limit=max_children, db_path=db_path)
    if not child_replies and fetch_children_remote:
        try:
            children = await with_flood_retry(
                lambda: client.get_messages(entity, limit=max_children, reply_to=target_msg.id),
                what="get_messages(replies)",
            )
            await pace()
            for child in children or []:
                if child and child.id != target_msg.id and (getattr(child, "message", None) or "").strip():
                    child_replies.append(message_to_snippet(child))
        except Exception as exc:
            log.debug("Could not fetch child replies for msg %s: %s", target_msg.id, exc)

    return ReplyThread(parent_messages=ancestors, child_replies=child_replies)


async def build_lead_context(
    client: TelegramClient,
    entity: Any,
    group_info: GroupInfo,
    target_msg: Message,
    context_msg_count: int = 5,
    db_path: str | None = None,
    use_local: bool = True,
    fetch_children_remote: bool = True,
) -> LeadContext:
    """Full context: author, preceding messages, reply chain and replies."""
    gid = group_info.group_id if use_local else None
    user = await extract_user_profile(client, target_msg)
    preceding = await fetch_preceding_messages(client, entity, target_msg.id, limit=context_msg_count, group_id=gid, db_path=db_path)
    thread = await fetch_reply_thread(
        client, entity, target_msg, group_id=gid, db_path=db_path, fetch_children_remote=fetch_children_remote
    )
    target_snippet = message_to_snippet(target_msg)
    if not target_snippet.sender_name:
        target_snippet.sender_name = user.display_name

    return LeadContext(
        lead_id=f"{group_info.group_id}_{target_msg.id}",
        group=group_info,
        target_message=target_snippet,
        user=user,
        previous_messages=preceding,
        reply_thread=thread,
        detected_at=now_utc(),
    )
