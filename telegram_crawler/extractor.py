"""Convert Telethon messages to the compact form stored in the archive."""

from __future__ import annotations

from typing import Any

from telethon.tl.types import Message

from telegram_crawler.models import MessageSnippet


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
