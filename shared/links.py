"""
Shared link canonicalization and formatting utilities (P08.05 / R40).

Pure, zero-dependency helper for normalizing group, community, and profile links.
"""

from __future__ import annotations

import re


def normalize_telegram_link(link: str) -> str:
    """
    Same group written differently (https://t.me/x, t.me/x/, @x, X) -> canonical key.
    Handles invite hashes (+hash, joinchat/hash) preserving exact casing,
    and public handles normalized to lowercase without leading @ or URLs.
    """
    s = (link or "").strip()
    s = re.sub(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/", "", s, flags=re.I).strip("/")
    s = s.lstrip("@")
    return s if s.startswith(("+", "joinchat/")) else s.lower()


def display_telegram_link(link: str) -> str:
    """
    Formats normalized key or raw link for user display:
    - @username for public groups/channels
    - https://t.me/+hash for private invite links
    - empty string if link is empty
    """
    key = normalize_telegram_link(link)
    if not key:
        return ""
    if key.startswith(("+", "joinchat/")):
        return f"https://t.me/{key}"
    return f"@{key}"


# Generic aliases
normalize_link = normalize_telegram_link
display_link = display_telegram_link
