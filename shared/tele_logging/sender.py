from __future__ import annotations

import os
import sys
from typing import Any

from shared.telegram_alerts import admin_chat_ids, proxy_candidates


def get_bot_token() -> str:
    """Returns the bot token dedicated to error alerts or falls back to config bot."""
    token = os.environ.get("TELEGRAM_ERROR_BOT_TOKEN", "").strip()
    if token:
        return token
    return os.environ.get("CONFIG_BOT_TOKEN", "").strip()


def get_target_chat_ids() -> list[int | str]:
    """Returns target chat/channel IDs for error alerts.

    Prefers TELEGRAM_ERROR_CHAT_ID (e.g., -100123456789 for an internal channel or group).
    Falls back to system admin chat IDs.
    """
    custom = os.environ.get("TELEGRAM_ERROR_CHAT_ID", "").strip()
    if custom:
        chats: list[int | str] = []
        for part in custom.split(","):
            part = part.strip()
            if not part:
                continue
            if part.lstrip("-").isdigit():
                chats.append(int(part))
            else:
                chats.append(part)
        if chats:
            return chats

    admins = admin_chat_ids()
    return list(admins) if admins else []


def send_telegram_raw(
    text: str,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True,
) -> bool:
    """Sends a raw text message to Telegram Bot API in a completely fail-safe manner.

    Guarantees:
    - Never raises an exception.
    - Never calls logging (avoids recursive error loops).
    - Uses configured proxies (e.g. xray) or direct connection.
    - Times out quickly (6s) to avoid lingering network resources.
    """
    token = get_bot_token()
    if not token:
        return False

    targets = get_target_chat_ids()
    if not targets:
        return False

    # Deferred import to ensure fast startup and avoid circular imports
    try:
        import requests
    except Exception:
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    proxies_to_try = proxy_candidates()
    debug_mode = os.environ.get("TELEGRAM_LOGGER_DEBUG", "0").strip().lower() in ("1", "true", "yes")

    any_success = False

    for chat_id in targets:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }

        for proxy_url in proxies_to_try:
            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            try:
                resp = requests.post(
                    url,
                    json=payload,
                    proxies=proxies,
                    timeout=6,
                )
                data = resp.json()
                if data.get("ok"):
                    any_success = True
                    break
                elif debug_mode:
                    sys.stderr.write(f"[tele_logging] Telegram API error: {data}\n")
            except Exception as exc:
                if debug_mode:
                    sys.stderr.write(f"[tele_logging] Proxy {proxy_url} failed: {exc}\n")
                continue

    return any_success
