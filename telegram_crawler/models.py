"""Data models of the Telegram crawler."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MessageSnippet(BaseModel):
    """Compact representation of a single Telegram message."""
    message_id: int
    sender_id: int | None = None
    sender_name: str | None = None
    text: str = ""
    date: datetime
    reply_to_msg_id: int | None = None


class GroupInfo(BaseModel):
    """Metadata of the group where the lead was found."""
    group_id: int
    title: str
    username: str | None = None
    invite_link: str | None = None
    is_supergroup: bool = True   # basic (legacy) groups have no t.me/c/... message links
