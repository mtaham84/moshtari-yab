"""
Shared Xray and V2Ray proxy URL parser (P08.07 / R40).

Pure parser module with zero side effects, no system commands, and no Django dependencies.
Supports: vless://, vmess://, trojan://, ss://, and throne:// URL formats.
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from typing import Any, Dict, Tuple


def validate_config_format(link: str) -> Tuple[bool, str, str]:
    """
    Validates proxy URL syntax and supported protocols.
    Returns: (is_valid: bool, scheme: str, error_message: str)
    """
    if not link or not isinstance(link, str) or not link.strip():
        return False, "", "متن کانفیگ خالی است."

    clean = link.strip()
    try:
        parsed = urllib.parse.urlparse(clean)
        scheme = parsed.scheme.lower()
        supported_schemes = {"vless", "vmess", "trojan", "ss", "throne"}

        if scheme not in supported_schemes:
            return False, scheme, f"پروتکل «{scheme}» پشتیبانی نمی‌شود. پروتکل‌های مجاز: vless, vmess, trojan, ss, throne"

        if scheme == "vmess":
            raw = clean[8:]
            raw += "=" * ((4 - len(raw) % 4) % 4)
            data = json.loads(base64.b64decode(raw).decode("utf-8"))
            if not data.get("add") or not data.get("port") or not data.get("id"):
                return False, scheme, "کانفیگ vmess فاقد آدرس، پورت یا آیدی معتبر است."
            return True, scheme, ""

        if scheme == "throne":
            raw = clean.split("throne://add/")[-1]
            raw += "=" * ((4 - len(raw) % 4) % 4)
            data = json.loads(base64.urlsafe_b64decode(raw).decode("utf-8"))
            if not data.get("server") or not data.get("password"):
                return False, scheme, "کانفیگ throne فاقد سرور یا پسورد است."
            return True, scheme, ""

        if not parsed.hostname:
            return False, scheme, "آدرس سرور یا هاست کانفیگ نامعتبر است."

        return True, scheme, ""
    except Exception as exc:
        return False, "", f"خطا در تجزیه فرمت کانفیگ: {exc}"


def parse_vless_url(url: str) -> Dict[str, Any]:
    """
    Parses a vless:// URL into an Xray outbound dict.
    """
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme.lower() != "vless":
        raise ValueError("Not a vless link")

    user_id = parsed.username
    host = parsed.hostname
    port = parsed.port or 443
    params = urllib.parse.parse_qs(parsed.query)

    tag = urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"vless_{host}_{port}"
    security = params.get("security", ["none"])[0]
    network = params.get("type", ["tcp"])[0]
    sni = params.get("sni", [host])[0]
    fp = params.get("fp", ["chrome"])[0]
    path = params.get("path", ["/"])[0]
    ws_host = params.get("host", [host])[0]

    stream_settings: Dict[str, Any] = {
        "network": network,
        "security": security,
    }

    if security == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": sni,
            "fingerprint": fp,
            "allowInsecure": False,
        }
    elif security == "reality":
        stream_settings["realitySettings"] = {
            "serverName": sni,
            "fingerprint": fp,
            "show": False,
            "publicKey": params.get("pbk", [""])[0],
            "shortId": params.get("sid", [""])[0],
            "spiderX": params.get("spx", ["/"])[0],
        }

    if network == "ws":
        stream_settings["wsSettings"] = {
            "path": path,
            "headers": {"Host": ws_host},
        }
    elif network == "grpc":
        stream_settings["grpcSettings"] = {
            "serviceName": params.get("serviceName", [""])[0],
        }
    elif network == "xhttp":
        extra_raw = params.get("extra", ["{}"])[0]
        try:
            extra = json.loads(extra_raw)
        except Exception:
            extra = {}
        stream_settings["xhttpSettings"] = {
            "mode": params.get("mode", ["stream-up"])[0],
            "path": path,
            "host": ws_host,
            "extra": extra,
        }

    enc = params.get("encryption", ["none"])[0]
    flow = params.get("flow", [""])[0]
    user_obj: Dict[str, Any] = {"id": user_id, "encryption": enc}
    if flow:
        user_obj["flow"] = flow

    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": int(port),
                    "users": [user_obj],
                }
            ]
        },
        "streamSettings": stream_settings,
    }


def parse_vmess_url(url: str) -> Dict[str, Any]:
    """
    Parses a vmess://base64 URL into an Xray outbound dict.
    """
    raw = url.strip()[8:]
    raw += "=" * ((4 - len(raw) % 4) % 4)
    data = json.loads(base64.b64decode(raw).decode("utf-8"))

    tag = data.get("ps", f"vmess_{data.get('add')}_{data.get('port')}")
    net = data.get("net", "tcp")
    sec = data.get("tls", "none")

    stream_settings: Dict[str, Any] = {
        "network": net,
        "security": sec if sec in ["tls", "reality"] else "none",
    }
    if sec == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": data.get("sni") or data.get("host") or data.get("add"),
            "allowInsecure": False,
        }
    if net == "ws":
        stream_settings["wsSettings"] = {
            "path": data.get("path", "/"),
            "headers": {"Host": data.get("host", data.get("add"))},
        }

    return {
        "tag": tag,
        "protocol": "vmess",
        "settings": {
            "vnext": [
                {
                    "address": data.get("add"),
                    "port": int(data.get("port", 443)),
                    "users": [
                        {
                            "id": data.get("id"),
                            "alterId": int(data.get("aid", 0)),
                            "security": "auto",
                        }
                    ],
                }
            ]
        },
        "streamSettings": stream_settings,
    }


def parse_trojan_url(url: str) -> Dict[str, Any]:
    """
    Parses a trojan://password@host:port URL into an Xray outbound dict.
    """
    parsed = urllib.parse.urlparse(url.strip())
    password = parsed.username
    host = parsed.hostname
    port = parsed.port or 443
    params = urllib.parse.parse_qs(parsed.query)

    tag = urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"trojan_{host}_{port}"
    sec = params.get("security", ["tls"])[0]
    net = params.get("type", ["tcp"])[0]
    sni = params.get("sni", [host])[0]

    stream_settings: Dict[str, Any] = {
        "network": net,
        "security": sec,
    }
    if sec == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": sni,
            "allowInsecure": False,
        }

    return {
        "tag": tag,
        "protocol": "trojan",
        "settings": {
            "servers": [
                {
                    "address": host,
                    "port": int(port),
                    "password": password,
                }
            ]
        },
        "streamSettings": stream_settings,
    }


def parse_ss_url(url: str) -> Dict[str, Any]:
    """
    Parses a standard ss://base64@host:port#tag URL into an Xray outbound dict.
    """
    parsed = urllib.parse.urlsplit(url.strip())
    userinfo = urllib.parse.unquote(parsed.username or parsed.netloc.split("@")[0])
    missing = len(userinfo) % 4
    if missing:
        userinfo += "=" * (4 - missing)
    try:
        decoded = base64.urlsafe_b64decode(userinfo).decode("utf-8")
    except Exception:
        decoded = base64.b64decode(userinfo).decode("utf-8")
    method, password = decoded.split(":", 1)
    tag = urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"ss_{parsed.hostname}_{parsed.port}"
    return {
        "tag": tag,
        "protocol": "shadowsocks",
        "settings": {
            "servers": [
                {
                    "address": parsed.hostname,
                    "port": int(parsed.port),
                    "method": method,
                    "password": password,
                }
            ]
        },
    }


def parse_throne_url(url: str) -> Dict[str, Any]:
    """
    Parses a throne://add/base64 URL into an Xray shadowsocks outbound dict.
    """
    raw = url.strip().split("throne://add/")[-1]
    missing = len(raw) % 4
    if missing:
        raw += "=" * (4 - missing)
    data = json.loads(base64.urlsafe_b64decode(raw).decode("utf-8"))
    tag = data.get("tag", f"ss_{data.get('server')}_{data.get('server_port')}")
    return {
        "tag": tag,
        "protocol": "shadowsocks",
        "settings": {
            "servers": [
                {
                    "address": data.get("server"),
                    "port": int(data.get("server_port", 1080)),
                    "method": data.get("method", "chacha20-ietf-poly1305"),
                    "password": data.get("password"),
                }
            ]
        },
    }


def parse_proxy_link(link: str) -> Dict[str, Any]:
    """
    Auto-detects and parses vless, vmess, trojan, ss, or throne links.
    Raises ValueError on unsupported schemes or invalid formats.
    """
    clean = (link or "").strip()
    if clean.startswith("vless://"):
        return parse_vless_url(clean)
    elif clean.startswith("vmess://"):
        return parse_vmess_url(clean)
    elif clean.startswith("trojan://"):
        return parse_trojan_url(clean)
    elif clean.startswith("ss://"):
        return parse_ss_url(clean)
    elif clean.startswith("throne://add/"):
        return parse_throne_url(clean)
    raise ValueError(f"پروتکل پشتیبانی نشده: {clean[:20]}...")
