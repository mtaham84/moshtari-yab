"""
Automated Telegram Channel Proxy Config Collector.
Strictly collects, tests, and imports proxy configs from: https://t.me/AR14N24B
Guards against unauthorized sources; only configs from this specific channel are permitted.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("discovery.channel_config_automation")

# STRICT ALLOWLIST: Under user security constraints, ONLY this channel is permitted.
AUTHORIZED_CHANNELS = ["AR14N24B"]

STATE_FILE = Path("/app/data/channel_configs_state.json")
if not STATE_FILE.parent.exists():
    STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "channel_configs_state.json"


def get_http_request_with_proxy(url: str, timeout: float = 12.0) -> str:
    """
    Fetches web content with direct attempt and fallback to local proxies
    (socks5h://xray:1080, http://xray:8080, 127.0.0.1).
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,fa;q=0.8",
    }

    candidates = ["direct"]
    if os.environ.get("TG_BOT_PROXY"):
        candidates.append(os.environ["TG_BOT_PROXY"].strip())
    candidates.extend([
        "socks5h://xray:1080",
        "http://xray:8080",
        "socks5h://127.0.0.1:1080",
        "http://127.0.0.1:8080",
    ])

    last_exc = None
    for proxy_choice in candidates:
        try:
            req = urllib.request.Request(url, headers=headers)
            if proxy_choice == "direct":
                opener = urllib.request.build_opener()
            else:
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({"http": proxy_choice, "https": proxy_choice})
                )
            with opener.open(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            last_exc = exc
            continue

    raise RuntimeError(f"Could not fetch {url} through direct or proxy channels: {last_exc}")


def load_channel_state(channel_username: str) -> Dict[str, Any]:
    """Loads incremental crawling state for the specified channel."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(channel_username, {"last_post_id": 0, "total_imported": 0})
        except Exception as e:
            logger.warning("Error reading channel state file: %s", e)
    return {"last_post_id": 0, "total_imported": 0}


def save_channel_state(channel_username: str, state_data: Dict[str, Any]) -> None:
    """Persists incremental crawling state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    all_data = {}
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                all_data = json.load(f)
        except Exception:
            all_data = {}
    all_data[channel_username] = state_data
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error writing channel state file: %s", e)


def fetch_channel_messages(channel_username: str = "AR14N24B") -> List[Dict[str, Any]]:
    """
    Fetches the latest public message wraps from https://t.me/s/{channel_username}.
    Strictly verifies channel username matches the authorized channel.
    """
    clean_channel = channel_username.strip().lstrip("@")
    if clean_channel.upper() not in [c.upper() for c in AUTHORIZED_CHANNELS]:
        raise PermissionError(
            f"Security Exception: Channel '{clean_channel}' is not authorized. "
            f"Only configs from {AUTHORIZED_CHANNELS} are permitted."
        )

    url = f"https://t.me/s/{clean_channel}"
    html = get_http_request_with_proxy(url)

    # Parse message blocks with data-post="CHANNEL/ID"
    # Example: data-post="AR14N24B/46079"
    pattern = re.compile(
        rf'class="[^"]*tgme_widget_message[^"]*"[^>]*data-post="{re.escape(clean_channel)}/(\d+)"([^>]*>[\s\S]*?)'
        rf'(?=<div class="[^"]*tgme_widget_message[^"]*"[^>]*data-post=|\s*<div class="tgme_footer"|$)',
        re.IGNORECASE
    )

    messages = []
    for match in pattern.finditer(html):
        post_id = int(match.group(1))
        content_html = match.group(2)
        messages.append({
            "channel": clean_channel,
            "post_id": post_id,
            "html": content_html,
        })

    # Sort in ascending order by post_id
    messages.sort(key=lambda m: m["post_id"])
    return messages


def extract_configs_from_text(raw_text_or_html: str) -> List[str]:
    """
    Extracts all supported proxy protocols (vless, vmess, trojan, ss) from text/html.
    Cleans trailing quotes, HTML entities, and query artifacts.
    """
    pattern = re.compile(r'(?:vless|vmess|trojan|ss)://[^\s<>\"\'`]+')
    matches = pattern.findall(raw_text_or_html)
    cleaned = []
    seen = set()

    for m in matches:
        # Strip trailing HTML tags or punctuation
        c = re.sub(r'[,\.;\)\(\]\[\'"]+$', '', m.strip())
        if c and c not in seen:
            seen.add(c)
            cleaned.append(c)
    return cleaned


def sync_channel_configs(
    channel_username: str = "AR14N24B",
    max_posts: int = 20,
    force_all: bool = False,
    max_add_per_run: int = 15,
) -> Dict[str, Any]:
    """
    Main pipeline:
    1. Enforces strict channel restriction (AR14N24B only).
    2. Fetches recent messages.
    3. Filters out already processed post IDs.
    4. Extracts proxy configs.
    5. Validates and tests each config live.
    6. Adds healthy configs into database and assigns Xray local ports.
    7. Updates state.
    """
    from xray.multi_proxy_manager import add_proxy_config

    clean_channel = channel_username.strip().lstrip("@")
    if clean_channel.upper() not in [c.upper() for c in AUTHORIZED_CHANNELS]:
        return {
            "success": False,
            "error": f"کانال '{clean_channel}' مجاز نمی‌باشد. طبق تنظیمات تنها کانال {AUTHORIZED_CHANNELS[0]} مجاز است.",
            "added_count": 0,
        }

    state = load_channel_state(clean_channel)
    last_post_id = 0 if force_all else state.get("last_post_id", 0)

    try:
        messages = fetch_channel_messages(clean_channel)
    except Exception as exc:
        logger.error("Failed to fetch messages for %s: %s", clean_channel, exc)
        return {
            "success": False,
            "error": f"خطا در دریافت پیام‌های کانال: {exc}",
            "added_count": 0,
        }

    # Filter messages newer than last_post_id
    new_messages = [m for m in messages if m["post_id"] > last_post_id]
    if max_posts > 0 and len(new_messages) > max_posts:
        new_messages = new_messages[-max_posts:]

    if not new_messages:
        return {
            "success": True,
            "channel": clean_channel,
            "message": "پیام یا کانفیگ جدیدی در کانال یافت نشد. وضعیت به‌روز است.",
            "posts_checked": 0,
            "configs_found": 0,
            "configs_tested": 0,
            "added_count": 0,
            "last_post_id": last_post_id,
        }

    configs_to_test = []
    max_seen_post_id = last_post_id

    for msg in new_messages:
        pid = msg["post_id"]
        if pid > max_seen_post_id:
            max_seen_post_id = pid
        cfgs = extract_configs_from_text(msg["html"])
        for c in cfgs:
            configs_to_test.append((pid, c))

    added_results = []
    failed_results = []

    for pid, raw_cfg in configs_to_test:
        if len(added_results) >= max_add_per_run:
            logger.info("Reached maximum additions cap (%d) for this cycle.", max_add_per_run)
            break

        tag_prefix = f"AR14_{pid}"
        try:
            res = add_proxy_config(raw_cfg, tag_prefix=tag_prefix)
            if res.get("success"):
                added_results.append({
                    "post_id": pid,
                    "protocol": res.get("protocol"),
                    "tag": res.get("tag"),
                    "ping_ms": res.get("ping_ms"),
                    "local_port": res.get("local_port"),
                })
                logger.info(
                    "✅ [AR14N24B] Healthy config imported: %s (Ping: %sms, Port: %s)",
                    res.get("tag"), res.get("ping_ms"), res.get("local_port")
                )
            else:
                failed_results.append({
                    "post_id": pid,
                    "reason": res.get("message"),
                })
        except Exception as exc:
            logger.warning("Error importing config from post #%s: %s", pid, exc)
            failed_results.append({"post_id": pid, "reason": str(exc)})

    # Update state with the highest post ID inspected
    state["last_post_id"] = max_seen_post_id
    state["total_imported"] = state.get("total_imported", 0) + len(added_results)
    state["last_sync_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_channel_state(clean_channel, state)

    return {
        "success": True,
        "channel": clean_channel,
        "posts_checked": len(new_messages),
        "configs_found": len(configs_to_test),
        "configs_tested": len(added_results) + len(failed_results),
        "added_count": len(added_results),
        "failed_count": len(failed_results),
        "added_configs": added_results,
        "last_post_id": max_seen_post_id,
        "message": (
            f"همگام‌سازی کانال @{clean_channel} تکمیل شد: "
            f"{len(added_results)} کانفیگ سالم تست و اضافه گردید (از مجموع {len(configs_to_test)} کانفیگ جدید)."
        ),
    }
