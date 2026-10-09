"""Sources the crawler watches: GLOBAL groups (defined by us, for every seller) and PRIVATE ones (one seller's).

Access rule, shared by need_engine and the panel: a need found in chat X is matched against
  • every seller's products when an active GLOBAL source exists for X,
  • otherwise only the products of sellers that have an active PRIVATE source for X,
  • nobody's when no active source exists for X (the chat is not analysed at all).
"""
from __future__ import annotations

import os
import re

GLOBAL, PRIVATE = "GLOBAL", "PRIVATE"
SCOPE_CHOICES = [(GLOBAL, "عمومی (برای همه‌ی فروشنده‌ها)"), (PRIVATE, "اختصاصی فروشنده")]

# Error codes written by the crawler into MonitoredCommunity.sync_error (see telegram_crawler/panel.py).
SYNC_ERROR_MESSAGES = {
    "INVALID_LINK": "این لینک یا آیدی در تلگرام وجود ندارد.",
    "NO_ACCESS": "گروه خصوصی است یا لینک دعوت منقضی شده است.",
    "NOT_A_GROUP": "این لینک مربوط به کاربر یا بات است، نه گروه یا کانال.",
    "FLOOD_WAIT": "تلگرام موقتاً محدودیت گذاشته؛ حدود {minutes} دقیقه دیگر خودکار دوباره تلاش می‌شود.",
    "BANNED": "حساب پایش از این گروه مسدود شده است.",
    "LIMIT_REACHED": "ظرفیت پایش گروه‌ها پر است؛ با پشتیبانی تماس بگیرید.",
    "UNKNOWN": "اتصال به گروه ممکن نشد.",
}


# A normalized key (see normalize_link) is either a public username or an invite hash.
TG_USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{3,31}$")
TG_INVITE_RE = re.compile(r"^(?:\+|joinchat/)[A-Za-z0-9_-]{5,}$")


def max_private_sources() -> int:
    try:
        return max(int(os.environ.get("TG_MAX_PRIVATE_SOURCES", "5")), 0)
    except ValueError:
        return 5


def normalize_link(link: str) -> str:
    """Same group written differently (https://t.me/x, t.me/x/, @x, X) → one key. Must match telegram_crawler.panel."""
    s = (link or "").strip()
    s = re.sub(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/", "", s, flags=re.I).strip("/")
    s = s.lstrip("@")
    return s if s.startswith(("+", "joinchat/")) else s.lower()


def display_link(link: str) -> str:
    """What we store/show: @username for public groups, the full URL for invite links."""
    key = normalize_link(link)
    if key.startswith(("+", "joinchat/")):
        return f"https://t.me/{key}"
    return f"@{key}" if key else ""


def sync_error_message(code: str | None) -> str:
    """Persian text for a crawler error code (``FLOOD_WAIT:<seconds>``, ``UNKNOWN:<detail>`` …)."""
    if not code:
        return ""
    head, _, arg = code.partition(":")
    if head not in SYNC_ERROR_MESSAGES:      # legacy free text from older crawler versions
        return SYNC_ERROR_MESSAGES["UNKNOWN"]
    if head == "FLOOD_WAIT":
        try:
            minutes = max(1, round(int(arg) / 60))
        except ValueError:
            minutes = 1
        return SYNC_ERROR_MESSAGES["FLOOD_WAIT"].format(minutes=minutes)
    return SYNC_ERROR_MESSAGES[head]
