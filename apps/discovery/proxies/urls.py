"""Proxy URLs: which proxy an account uses, how it is shown, and how operator input is cleaned and validated.

Every Instagram account talks through a proxy: its own (``custom``), a system V2Ray config (``config``), or the
shared xray pool (``pool``), never the server's own IP. A ``#rotate=<url>`` fragment on a proxy URL is the IP
rotation link of a mobile proxy; HTTP clients must never see it.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from django.conf import settings

XRAY_HOST = "xray"
POOL_PORT = 1080
RESERVED_PORTS = (1080, 8080)          # xray's shared pool inbounds, never a dedicated config
PROXY_SCHEMES = ("http", "https", "socks5", "socks5h", "socks4", "socks4a")


def clean_proxy_url(raw_url: str) -> str:
    """Strips auxiliary fragments like #rotate=... so HTTP clients get a valid URI."""
    if not raw_url:
        return ""
    return raw_url.split("#rotate=")[0].strip()


def extract_rotation_url(account_or_url: Any) -> str:
    """
    Extracts the IP rotation webhook / API link from:
    1. An account's device_settings.proxy_meta.rotation_url.
    2. A '#rotate=http...' fragment in the raw proxy_url string.
    """
    if hasattr(account_or_url, "device_settings") and isinstance(account_or_url.device_settings, dict):
        pm = account_or_url.device_settings.get("proxy_meta")
        if isinstance(pm, dict) and pm.get("rotation_url"):
            return str(pm["rotation_url"]).strip()

    raw_url = getattr(account_or_url, "proxy_url", "") or str(account_or_url or "")
    if "#rotate=" in raw_url:
        return raw_url.split("#rotate=", 1)[1].strip()

    return ""


def default_proxy_url() -> str:
    """Proxy for accounts without their own proxy or config ("direct"/"none" = server IP, not recommended)."""
    raw = str(getattr(settings, "INSTAGRAM_DEFAULT_PROXY_URL", f"socks5h://{XRAY_HOST}:{POOL_PORT}") or "").strip()
    return "" if raw.lower() in ("", "direct", "none", "off") else raw


def effective_proxy_url(account) -> str:
    """The proxy every Instagram client of `account` must use (its own/config, else the shared pool)."""
    return clean_proxy_url(getattr(account, "proxy_url", "") or "") or default_proxy_url()


def xray_port(url) -> int | None:
    """Port of an xray inbound URL (socks5h://xray:1083), None for any other proxy."""
    try:
        parsed = urlparse(clean_proxy_url(url))
        host = (parsed.hostname or "").lower()
        if host not in (XRAY_HOST, "127.0.0.1", "localhost"):
            return None
        return parsed.port
    except ValueError:
        return None


def config_url(config) -> str:
    return f"socks5h://{XRAY_HOST}:{config.local_port}"


def proxy_mode(account) -> str:
    """config (system V2Ray config) | custom (its own proxy) | legacy (xray port without config link) | pool."""
    if account.proxy_config_id:
        return "config"
    url = clean_proxy_url(account.proxy_url)
    if not url:
        return "pool"
    port = xray_port(url)
    if port is None:
        return "custom"
    return "pool" if port in RESERVED_PORTS else "legacy"


def proxy_label(account) -> str:
    mode = proxy_mode(account)
    if mode == "config":
        cfg = account.proxy_config
        return f"کانفیگ سیستم #{cfg.pk} ({cfg.tag or cfg.server}) · پورت {cfg.local_port}"
    if mode == "custom":
        parsed = urlparse(clean_proxy_url(account.proxy_url))
        return f"پروکسی اختصاصی {parsed.scheme}://{parsed.hostname}:{parsed.port or ''}"
    if mode == "legacy":
        return f"پورت xray {xray_port(account.proxy_url)} (کانفیگش پیدا نشد)"
    return "استخر مشترک سیستم" if default_proxy_url() else "بدون پروکسی (IP سرور)"


def normalize_proxy_url(raw) -> str:
    """http/socks URL; also accepts host:port and host:port:user:pass (as sold by most proxy providers)."""
    url = str(raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        parts = url.split(":")
        if len(parts) == 2:
            url = f"http://{parts[0]}:{parts[1]}"
        elif len(parts) == 4:
            url = f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
    return url


def validate_proxy_url(url) -> str:
    """Error text, or "" when `url` is a usable http/socks proxy URL."""
    try:
        parsed = urlparse(clean_proxy_url(url))
        port = parsed.port
    except ValueError:
        return "فرمت پروکسی نامعتبر است."
    if parsed.scheme.lower() not in PROXY_SCHEMES:
        return "پروکسی باید با http:// یا socks5:// شروع شود (لینک vless/vmess را در «کانفیگ V2Ray» وارد کنید)."
    if not parsed.hostname or not port:
        return "آدرس و پورت پروکسی کامل نیست."
    return ""

