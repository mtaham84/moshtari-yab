"""Talking to the Telegram Bot API through a working proxy (the API is filtered), and the subscriber list."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from shared.telegram_alerts import load_subscribers, save_subscribers

log = logging.getLogger("telegram_config_bot")

# The bot token lives only in .env (CONFIG_BOT_TOKEN); workers/telegram_config_bot.py refuses to start without it.
BOT_TOKEN = os.environ.get("CONFIG_BOT_TOKEN", "").strip()


TG_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


_proxy_cache: tuple[float, list[str]] = (0.0, [])


_last_working_proxy: str | None = None


def get_proxy_candidates() -> list[str]:
    """Returns list of proxy candidates to try for Telegram API, cached for 60s."""
    global _proxy_cache
    now = time.time()
    if _proxy_cache[1] and (now - _proxy_cache[0]) < 60:
        candidates = list(_proxy_cache[1])
    else:
        candidates = []
        if os.environ.get("TG_BOT_PROXY"):
            candidates.append(os.environ["TG_BOT_PROXY"].strip())
        if os.environ.get("TG_PROXY"):
            candidates.append(os.environ["TG_PROXY"].strip())

        in_docker = Path("/.dockerenv").exists()
        host = "xray" if in_docker else "127.0.0.1"

        candidates.extend([f"socks5h://{host}:1080", f"http://{host}:8080"])

        # Also add active dedicated ports from DB if Django is ready
        try:
            from apps.discovery.models import ProxyConfig
            from django.db import close_old_connections
            close_old_connections()
            active_ports = list(
                ProxyConfig.objects.filter(status="ACTIVE")
                .exclude(local_port__isnull=True)
                .values_list("local_port", flat=True)[:5]
            )
            for p in active_ports:
                candidates.append(f"socks5h://{host}:{p}")
        except Exception:
            log.debug("Could not read proxy config ports from the database", exc_info=True)

        candidates = list(dict.fromkeys(candidates))
        _proxy_cache = (now, candidates)

    # Prioritize last working proxy if known
    if _last_working_proxy and _last_working_proxy in candidates:
        candidates.remove(_last_working_proxy)
        candidates.insert(0, _last_working_proxy)

    return candidates


def tg_request(method: str, payload: dict[str, Any] | None = None, timeout: int = 35) -> dict[str, Any] | None:
    """Sends a request to Telegram Bot API via requests library using local proxy."""
    global _last_working_proxy
    import requests
    url = f"{TG_API_BASE}/{method}"
    candidates = get_proxy_candidates()

    last_exc = None
    # Connect timeout max 4s to skip dead proxies quickly; read timeout stays up to `timeout`
    req_timeout = (4.0, float(timeout))
    for proxy_url in candidates:
        proxies = {"http": proxy_url, "https": proxy_url}
        try:
            if payload:
                resp = requests.post(url, json=payload, proxies=proxies, timeout=req_timeout)
            else:
                resp = requests.get(url, proxies=proxies, timeout=req_timeout)

            data = resp.json()
            if not data.get("ok"):
                log.warning("Telegram API error (%s): %s", method, data)
            _last_working_proxy = proxy_url
            return data
        except Exception as exc:
            log.debug("Telegram %s through %s failed", method, proxy_url, exc_info=True)
            last_exc = exc
            if _last_working_proxy == proxy_url:
                _last_working_proxy = None
            continue

    log.warning("Telegram request failed (%s) across all proxy candidates: %s", method, last_exc)
    return None


def send_message(chat_id: int | str, text: str, reply_markup: dict[str, Any] | None = None, parse_mode: str = "HTML") -> None:
    """Sends a formatted message to a Telegram chat, auto-chunking if exceeding 3800 chars."""
    if not text:
        return

    MAX_CHUNK = 3800
    if len(text) <= MAX_CHUNK:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        tg_request("sendMessage", payload, timeout=12)
        return

    lines = text.split("\n")
    chunks = []
    current_chunk = []
    current_len = 0

    for line in lines:
        if current_len + len(line) + 1 > MAX_CHUNK and current_chunk:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = len(line)
        else:
            current_chunk.append(line)
            current_len += len(line) + 1

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    for idx, chunk in enumerate(chunks):
        is_last = (idx == len(chunks) - 1)
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if is_last and reply_markup:
            payload["reply_markup"] = reply_markup
        tg_request("sendMessage", payload, timeout=12)
        if not is_last:
            time.sleep(0.15)


def get_subscribers() -> set[int]:
    return load_subscribers()


def register_subscriber(chat_id: int | str) -> None:
    try:
        cid = int(chat_id)
        current = get_subscribers()
        if cid not in current:
            current.add(cid)
            save_subscribers(current)
    except Exception as e:
        log.warning("Could not register subscriber %s: %s", chat_id, e, exc_info=True)


def broadcast_message(text: str, reply_markup: dict[str, Any] | None = None) -> None:
    for cid in get_subscribers():
        try:
            send_message(cid, text, reply_markup=reply_markup)
            time.sleep(0.05)
        except Exception as e:
            log.warning("Failed sending broadcast to %s: %s", cid, e, exc_info=True)
