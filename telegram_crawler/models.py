"""Data models of the Telegram crawler."""

from __future__ import annotations

from pydantic import BaseModel


class GroupInfo(BaseModel):
    """A resolved group: ``group_id`` is the marked peer id (-100… for supergroups)."""
    group_id: int
    title: str
    username: str | None = None
    invite_link: str | None = None
    is_supergroup: bool = True   # basic (legacy) groups have no t.me/c/... message links
