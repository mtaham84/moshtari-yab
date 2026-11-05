"""Telegram alert settings shared by the web app and the config bot.

Everything here is read from the environment (``.env`` is already loaded by
``config/settings.py`` and by docker compose), so neither side has to parse
``.env`` by hand or hard-code host paths such as ``/root/moshtari-yab``.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent

# Kept as the default so existing deployments keep alerting the same admins.
_DEFAULT_ADMIN_CHAT_IDS = "1245164686,1817086635"
_DEFAULT_FALLBACK_PROXIES = "socks5h://127.0.0.1:1080,socks5h://xray:1080,http://127.0.0.1:8080"


def _parse_chat_ids(raw: str) -> set[int]:
    chats: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            chats.add(int(part))
    return chats


def admin_chat_ids() -> set[int]:
    """Admin chats from ``TELEGRAM_ADMIN_CHAT_IDS`` (comma separated)."""
    return _parse_chat_ids(os.environ.get("TELEGRAM_ADMIN_CHAT_IDS", _DEFAULT_ADMIN_CHAT_IDS))


def subscribers_file() -> Path:
    """Where the bot stores subscriber chat ids (``BOT_SUBSCRIBERS_FILE``)."""
    configured = os.environ.get("BOT_SUBSCRIBERS_FILE", "").strip()
    return Path(configured) if configured else REPO_ROOT / "data" / "bot_subscribers.json"


def load_subscribers() -> set[int]:
    """Admin chats plus every chat that subscribed through the bot."""
    chats = admin_chat_ids()
    path = subscribers_file()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                chats.update(int(c) for c in data if str(c).lstrip("-").isdigit())
    except Exception as exc:  # unreadable file should not block alerts
        logger.warning("Could not read subscribers from %s: %s", path, exc)
    return chats


def save_subscribers(chats: set[int]) -> None:
    path = subscribers_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(chats)), encoding="utf-8")


def proxy_candidates() -> list[str | None]:
    """Proxies to try in order, without duplicates; ``None`` means direct.

    ``TG_BOT_PROXY`` (if set) comes first, then ``TELEGRAM_ALERT_FALLBACK_PROXIES``,
    then one direct attempt.
    """
    configured = os.environ.get("TG_BOT_PROXY", "").strip()
    fallbacks = os.environ.get("TELEGRAM_ALERT_FALLBACK_PROXIES", _DEFAULT_FALLBACK_PROXIES)
    ordered = [configured, *(p.strip() for p in fallbacks.split(","))]
    return list(dict.fromkeys(p for p in ordered if p)) + [None]
