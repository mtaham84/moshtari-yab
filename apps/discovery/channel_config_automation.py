"""
Automated Telegram Channel Proxy Config Collector & Channel Manager.
Enables dynamic configuration of Telegram channel sources to extract,
test, and import only newly published proxy configurations.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("discovery.channel_config_automation")

CHANNELS_JSON_FILE = Path("/app/data/proxy_channels.json")
if not CHANNELS_JSON_FILE.parent.exists():
    CHANNELS_JSON_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "proxy_channels.json"

STATE_FILE = Path("/app/data/channel_configs_state.json")
if not STATE_FILE.parent.exists():
    STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "channel_configs_state.json"


def normalize_channel_username(channel_str: str) -> str:
    """
    Cleans raw channel input into normalized channel username.
    Supports:
        - '@AR14N24B' -> 'AR14N24B'
        - 'https://t.me/AR14N24B' -> 'AR14N24B'
        - 'https://t.me/s/AR14N24B' -> 'AR14N24B'
        - 't.me/AR14N24B' -> 'AR14N24B'
        - 'AR14N24B' -> 'AR14N24B'
    """
    if not channel_str:
        return ""
    clean = channel_str.strip()
    clean = re.sub(r"^https?://(?:www\.)?t\.me/(?:s/)?", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^t\.me/(?:s/)?", "", clean, flags=re.IGNORECASE)
    clean = clean.lstrip("@").strip().rstrip("/")
    # Filter valid telegram username chars (a-z, 0-9, _)
    match = re.match(r"^[a-zA-Z0-9_]{3,64}$", clean)
    if not match:
        raise ValueError(f"شناسه کانال نامعتبر است: '{channel_str}'. باید حداقل ۳ حرف و فقط حروف انگلیسی، عدد یا آندرلاین باشد.")
    return clean


def _load_channels_json() -> List[Dict[str, Any]]:
    CHANNELS_JSON_FILE.parent.mkdir(parents=True, exist_ok=True)
    if CHANNELS_JSON_FILE.exists():
        try:
            with open(CHANNELS_JSON_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.warning("Error reading proxy channels JSON: %s", e)
    # Default seed channel
    return [
        {
            "id": 1,
            "channel_username": "AR14N24B",
            "title": "کانال اصلی کانفیگ",
            "is_active": True,
            "last_synced_at": None,
            "total_configs_found": 0,
        }
    ]


def _save_channels_json(channels: List[Dict[str, Any]]) -> None:
    CHANNELS_JSON_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CHANNELS_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(channels, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Error writing proxy channels JSON: %s", e)


def get_monitored_channels() -> List[Dict[str, Any]]:
    """
    Returns list of monitored channels.
    Tries database model ConfigChannelSource first, falls back to JSON file.
    """
    try:
        from apps.discovery.models.proxy import ConfigChannelSource
        channels_qs = ConfigChannelSource.objects.all().order_by("-is_active", "-created_at")
        results = []
        for ch in channels_qs:
            results.append({
                "id": ch.id,
                "channel_username": ch.channel_username,
                "title": ch.title or f"کانال @{ch.channel_username}",
                "is_active": ch.is_active,
                "last_synced_at": ch.last_synced_at.isoformat() if ch.last_synced_at else None,
                "total_configs_found": ch.total_configs_found,
                "telegram_link": ch.telegram_link,
            })
        if results:
            _save_channels_json(results)
            return results
    except Exception as exc:
        logger.debug("Database ConfigChannelSource query unavailable, using JSON: %s", exc)

    json_channels = _load_channels_json()
    for item in json_channels:
        item["telegram_link"] = f"https://t.me/{item['channel_username'].lstrip('@')}"
    return json_channels


def add_monitored_channel(channel_str: str, title: str = "") -> Dict[str, Any]:
    """Adds a new channel to monitoring pool."""
    username = normalize_channel_username(channel_str)
    title = title.strip() or f"کانال @{username}"

    # Try database creation
    try:
        from apps.discovery.models.proxy import ConfigChannelSource
        ch, created = ConfigChannelSource.objects.get_or_create(
            channel_username=username,
            defaults={"title": title, "is_active": True},
        )
        if not created and not ch.is_active:
            ch.is_active = True
            ch.save(update_fields=["is_active"])
        channel_dict = {
            "id": ch.id,
            "channel_username": ch.channel_username,
            "title": ch.title,
            "is_active": ch.is_active,
            "last_synced_at": ch.last_synced_at.isoformat() if ch.last_synced_at else None,
            "total_configs_found": ch.total_configs_found,
            "telegram_link": ch.telegram_link,
        }
        # Update JSON mirror
        channels = _load_channels_json()
        if not any(c["channel_username"].lower() == username.lower() for c in channels):
            channels.append(channel_dict)
            _save_channels_json(channels)
        return channel_dict
    except Exception as exc:
        logger.debug("Using JSON file fallback for add_monitored_channel: %s", exc)

    channels = _load_channels_json()
    for ch in channels:
        if ch["channel_username"].lower() == username.lower():
            ch["is_active"] = True
            ch["title"] = title
            _save_channels_json(channels)
            ch["telegram_link"] = f"https://t.me/{username}"
            return ch

    new_id = max([c.get("id", 0) for c in channels], default=0) + 1
    new_entry = {
        "id": new_id,
        "channel_username": username,
        "title": title,
        "is_active": True,
        "last_synced_at": None,
        "total_configs_found": 0,
        "telegram_link": f"https://t.me/{username}",
    }
    channels.append(new_entry)
    _save_channels_json(channels)
    return new_entry


def delete_monitored_channel(channel_id_or_username: Any) -> bool:
    """Removes a channel from monitoring."""
    target_id = None
    target_username = None
    if isinstance(channel_id_or_username, int) or (isinstance(channel_id_or_username, str) and str(channel_id_or_username).isdigit()):
        target_id = int(channel_id_or_username)
    else:
        target_username = str(channel_id_or_username).lstrip("@").strip().lower()

    # Try DB deletion
    try:
        from apps.discovery.models.proxy import ConfigChannelSource
        if target_id:
            ConfigChannelSource.objects.filter(pk=target_id).delete()
        elif target_username:
            ConfigChannelSource.objects.filter(channel_username__iexact=target_username).delete()
    except Exception as exc:
        logger.debug("DB channel deletion skipped: %s", exc)

    channels = _load_channels_json()
    filtered = []
    found = False
    for ch in channels:
        match_id = target_id and ch.get("id") == target_id
        match_un = target_username and ch.get("channel_username", "").lower() == target_username
        if match_id or match_un:
            found = True
        else:
            filtered.append(ch)

    if found or not target_id:
        _save_channels_json(filtered)
    return True


def toggle_monitored_channel(channel_id: int) -> Dict[str, Any]:
    """Toggles active/paused state of a channel."""
    # Try DB update
    try:
        from apps.discovery.models.proxy import ConfigChannelSource
        ch = ConfigChannelSource.objects.get(pk=channel_id)
        ch.is_active = not ch.is_active
        ch.save(update_fields=["is_active"])
        return {"id": ch.id, "channel_username": ch.channel_username, "is_active": ch.is_active}
    except Exception as exc:
        logger.debug("DB channel toggle skipped: %s", exc)

    channels = _load_channels_json()
    for ch in channels:
        if ch.get("id") == int(channel_id):
            ch["is_active"] = not ch.get("is_active", True)
            _save_channels_json(channels)
            return ch
    raise ValueError(f"کانال با شناسه {channel_id} یافت نشد.")


def get_http_request_with_proxy(url: str, timeout: float = 12.0) -> str:
    """Fetches web content with direct attempt and fallback to local proxies."""
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


def fetch_channel_messages(channel_username: str) -> List[Dict[str, Any]]:
    """
    Fetches the latest public message wraps from https://t.me/s/{channel_username}.
    """
    clean_channel = normalize_channel_username(channel_username)
    url = f"https://t.me/s/{clean_channel}"
    html = get_http_request_with_proxy(url)

    # Parse message blocks with data-post="CHANNEL/ID"
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
        c = re.sub(r'[,\.;\)\(\]\[\'"]+$', '', m.strip())
        if c and c not in seen:
            seen.add(c)
            cleaned.append(c)
    return cleaned


def sync_channel_configs(
    channel_username: str,
    max_posts: int = 20,
    force_all: bool = False,
    max_add_per_run: int = 15,
) -> Dict[str, Any]:
    """
    Crawls a single channel, extracting and adding ONLY NEW configs not already in database.
    """
    from xray.multi_proxy_manager import add_proxy_config

    clean_channel = normalize_channel_username(channel_username)
    state = load_channel_state(clean_channel)
    last_post_id = 0 if force_all else state.get("last_post_id", 0)

    try:
        messages = fetch_channel_messages(clean_channel)
    except Exception as exc:
        logger.error("Failed to fetch messages for %s: %s", clean_channel, exc)
        return {
            "success": False,
            "channel": clean_channel,
            "error": f"خطا در دریافت پیام‌های کانال @{clean_channel}: {exc}",
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
            "message": f"پیام یا کانفیگ جدیدی در کانال @{clean_channel} یافت نشد (تا پست #{last_post_id}).",
            "posts_checked": 0,
            "configs_found": 0,
            "configs_tested": 0,
            "added_count": 0,
            "last_post_id": last_post_id,
        }

    # Build set of existing raw URLs in database to ensure we ONLY pick up NEW configs
    existing_urls = set()
    try:
        from apps.discovery.models.proxy import ProxyConfig
        existing_urls = set(ProxyConfig.objects.values_list("raw_url", flat=True))
    except Exception:
        pass

    configs_to_test = []
    max_seen_post_id = last_post_id
    skipped_duplicate_count = 0

    for msg in new_messages:
        pid = msg["post_id"]
        if pid > max_seen_post_id:
            max_seen_post_id = pid
        cfgs = extract_configs_from_text(msg["html"])
        for c in cfgs:
            if c in existing_urls:
                skipped_duplicate_count += 1
                continue
            configs_to_test.append((pid, c))

    added_results = []
    failed_results = []

    for pid, raw_cfg in configs_to_test:
        if len(added_results) >= max_add_per_run:
            logger.info("Reached maximum additions cap (%d) for channel @%s.", max_add_per_run, clean_channel)
            break

        tag_prefix = f"{clean_channel}_{pid}"
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
                    "✅ [@%s] New healthy config imported: %s (Ping: %sms, Port: %s)",
                    clean_channel, res.get("tag"), res.get("ping_ms"), res.get("local_port")
                )
            else:
                failed_results.append({
                    "post_id": pid,
                    "reason": res.get("message"),
                })
        except Exception as exc:
            logger.warning("Error importing config from post #%s: %s", pid, exc)
            failed_results.append({"post_id": pid, "reason": str(exc)})

    # Update crawl state
    state["last_post_id"] = max_seen_post_id
    state["total_imported"] = state.get("total_imported", 0) + len(added_results)
    state["last_sync_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save_channel_state(clean_channel, state)

    # Update channel model / JSON statistics
    try:
        from apps.discovery.models.proxy import ConfigChannelSource
        from django.utils import timezone as dj_tz
        ch_obj = ConfigChannelSource.objects.filter(channel_username__iexact=clean_channel).first()
        if ch_obj:
            ch_obj.last_synced_at = dj_tz.now()
            ch_obj.total_configs_found += len(added_results)
            ch_obj.save(update_fields=["last_synced_at", "total_configs_found"])
    except Exception:
        pass

    channels = _load_channels_json()
    for ch in channels:
        if ch.get("channel_username", "").lower() == clean_channel.lower():
            ch["last_synced_at"] = datetime.now(timezone.utc).isoformat()
            ch["total_configs_found"] = ch.get("total_configs_found", 0) + len(added_results)
            _save_channels_json(channels)
            break

    return {
        "success": True,
        "channel": clean_channel,
        "posts_checked": len(new_messages),
        "configs_found": len(configs_to_test) + skipped_duplicate_count,
        "configs_new": len(configs_to_test),
        "skipped_duplicate_count": skipped_duplicate_count,
        "configs_tested": len(added_results) + len(failed_results),
        "added_count": len(added_results),
        "failed_count": len(failed_results),
        "added_configs": added_results,
        "last_post_id": max_seen_post_id,
        "message": (
            f"همگام‌سازی کانال @{clean_channel} تکمیل شد: "
            f"{len(added_results)} کانفیگ جدید و سالم اضافه شد "
            f"({skipped_duplicate_count} کانفیگ تکراری نادیده گرفته شد)."
        ),
    }


def sync_all_active_channels(max_posts: int = 20, max_add_per_channel: int = 10) -> Dict[str, Any]:
    """Syncs all active channels sequentially."""
    channels = get_monitored_channels()
    active_channels = [c for c in channels if c.get("is_active", True)]

    if not active_channels:
        return {
            "success": True,
            "message": "هیچ کانال فعالی برای بررسی ثبت نشده است.",
            "total_channels": 0,
            "total_added": 0,
            "results": [],
        }

    results = []
    total_added = 0
    for ch in active_channels:
        res = sync_channel_configs(
            ch["channel_username"],
            max_posts=max_posts,
            max_add_per_run=max_add_per_channel,
        )
        results.append(res)
        total_added += res.get("added_count", 0)

    return {
        "success": True,
        "total_channels": len(active_channels),
        "total_added": total_added,
        "results": results,
        "message": f"بررسی {len(active_channels)} کانال با موفقیت انجام شد و مجموعاً {total_added} کانفیگ جدید ثبت گردید.",
    }
