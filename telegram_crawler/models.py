"""Pydantic data models for structured lead extraction."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class UserProfile(BaseModel):
    """Detailed profile of the message author."""
    user_id: int
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None
    phone: str | None = None
    is_bot: bool = False
    is_premium: bool = False

    @property
    def display_name(self) -> str:
        parts = [self.first_name or "", self.last_name or ""]
        name = " ".join(p for p in parts if p).strip()
        if not name and self.username:
            return f"@{self.username}"
        return name or f"User {self.user_id}"


class MessageSnippet(BaseModel):
    """Compact representation of a single Telegram message."""
    message_id: int
    sender_id: int | None = None
    sender_name: str | None = None
    text: str = ""
    date: datetime
    reply_to_msg_id: int | None = None


class ReplyThread(BaseModel):
    """The thread context of the target message."""
    parent_messages: list[MessageSnippet] = Field(
        default_factory=list,
        description="Ancestors of this message (tracing reply_to_msg_id back to root)."
    )
    child_replies: list[MessageSnippet] = Field(
        default_factory=list,
        description="Direct replies to this message from other users in the group."
    )


class GroupInfo(BaseModel):
    """Metadata of the group where the lead was found."""
    group_id: int
    title: str
    username: str | None = None
    invite_link: str | None = None


class LeadContext(BaseModel):
    """Comprehensive structured payload representing a discovered lead."""
    lead_id: str
    group: GroupInfo
    target_message: MessageSnippet
    user: UserProfile
    previous_messages: list[MessageSnippet] = Field(
        default_factory=list,
        description="Recent context messages before the target message in the chat."
    )
    reply_thread: ReplyThread
    detected_at: datetime = Field(default_factory=now_utc)
    metadata: dict[str, Any] = Field(default_factory=dict)
