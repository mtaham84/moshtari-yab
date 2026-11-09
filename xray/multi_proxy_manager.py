#!/usr/bin/env python3
"""
Multi-Proxy Manager & Xray Port-Routing Controller
- Manages separate proxy connections for each scraper account (Instagram & Twitter/X)
- Each healthy proxy gets a dedicated local SOCKS5 inbound port (e.g. 1081, 1082, 1083...)
- Parses and strictly validates vless://, vmess://, trojan://, ss://, throne:// links
- Measures real-time latency (ping_ms) against Google/Telegram/Instagram
- Automatically prunes dead configs (ping == -1 / unreachable) from database and Xray
- Dynamically generates xray/config.json with routing rules and restarts Xray container
"""
from __future__ import annotations

import base64
import html
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import urllib.parse
from typing import Any

log = logging.getLogger("multi_proxy_manager")

def _get_base_dir() -> Path:
    env_dir = os.environ.get("XRAY_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parent

BASE_DIR = _get_base_dir()
CONFIG_FILE = BASE_DIR / "config.json"


def _find_xray_bin() -> Path:
    env_bin = os.environ.get("XRAY_BIN")
    if env_bin and Path(env_bin).exists():
        return Path(env_bin)
    which_bin = shutil.which("xray")
    if which_bin and Path(which_bin).exists():
        return Path(which_bin)
    for candidate in [
        Path("/usr/local/bin/xray"),
        Path("/usr/bin/xray"),
        Path.home() / ".local" / "bin" / "xray",
        Path("/opt/happ/bin/core/xray"),
        Path("/opt/v2rayN/bin/xray/xray"),
        Path.home() / ".local" / "share" / "v2rayN" / "bin" / "xray" / "xray",
        Path(__file__).resolve().parent / "bin" / "xray",
    ]:
        if candidate.exists():
            return candidate
    return Path("/usr/local/bin/xray")


XRAY_BIN = _find_xray_bin()

PROBE_URL = os.environ.get("PROBE_URL", "https://api.telegram.org")
BASE_LOCAL_PORT = 1081


def _redirect_stays_on_probe_domain(probe_url: str, location: str) -> bool:
    """True when a redirect points to the probe host's own domain (api.telegram.org -> core.telegram.org).

    A filtering box redirects to a block page on another host, which must not count as a working tunnel.
    """
    probe_host = (urllib.parse.urlsplit(probe_url).hostname or "").lower()
    target_host = (urllib.parse.urlsplit(urllib.parse.urljoin(probe_url, location)).hostname or "").lower()
    domain = ".".join(probe_host.split(".")[-2:])
    return bool(domain) and (target_host == domain or target_host.endswith("." + domain))


def probe_socks5_http(proxy_url: str, probe_url: str = PROBE_URL, timeout_sec: float = 2.5) -> tuple[bool, float, str]:
    """
    Performs an end-to-end HTTP GET probe through a socks5 proxy to probe_url.
    Returns (is_connected: bool, ping_ms: float, error_message: str).
    Alive: HTTP 200/204, or a redirect that stays on the probe URL's domain (api.telegram.org answers 302 to
    core.telegram.org), within latency <= 2000ms. Redirects elsewhere and error codes (4xx/5xx) are rejected.
    """
    import requests
    t0 = time.perf_counter()
    proxies = {"http": proxy_url, "https": proxy_url}
    try:
        resp = requests.get(
            probe_url,
            proxies=proxies,
            timeout=timeout_sec,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            allow_redirects=False,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        reached = resp.status_code in (200, 204) or (
            resp.status_code in (301, 302, 303, 307, 308)
            and _redirect_stays_on_probe_domain(probe_url, resp.headers.get("Location", ""))
        )
        if reached:
            if elapsed_ms > 2000.0:
                return False, -1.0, f"پینگ بالا ({elapsed_ms:.0f}ms > ۲۰۰۰ms)"
            return True, round(elapsed_ms, 1), ""
        return False, -1.0, f"کد وضعیت نامعتبر یا فیلترشده: HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        return False, -1.0, f"تایم‌اوت اتصال (بیش از {timeout_sec:.1f} ثانیه)"
    except Exception as exc:
        return False, -1.0, f"خطای ارتباط پروکسی: {exc}"


def probe_proxy_port(local_port: int, probe_url: str = PROBE_URL, timeout_sec: float = 2.5) -> tuple[bool, float, str]:
    """Probes dedicated local port socks5h://{xray_host}:{local_port}."""
    in_docker = Path("/.dockerenv").exists()
    host = os.environ.get("XRAY_HOST", "xray" if in_docker else "127.0.0.1")
    return probe_socks5_http(f"socks5h://{host}:{local_port}", probe_url=probe_url, timeout_sec=timeout_sec)

def backup_tunnel() -> dict:
    """Fallback "default_proxy" outbound used when no proxy config is available.

    Credentials come from the environment (XRAY_BACKUP_SS_ADDRESS / _PORT / _METHOD / _PASSWORD); nothing is
    hard-coded in the repository. Without them the fallback is a plain direct outbound (logged)."""
    address = os.environ.get("XRAY_BACKUP_SS_ADDRESS", "").strip()
    password = os.environ.get("XRAY_BACKUP_SS_PASSWORD", "").strip()
    if not address or not password:
        log.warning("XRAY_BACKUP_SS_ADDRESS/XRAY_BACKUP_SS_PASSWORD not set; default_proxy falls back to direct")
        return {"tag": "default_proxy", "protocol": "freedom"}
    return {
        "tag": "default_proxy",
        "protocol": "shadowsocks",
        "settings": {
            "servers": [
                {
                    "address": address,
                    "port": int(os.environ.get("XRAY_BACKUP_SS_PORT", "443") or 443),
                    "method": os.environ.get("XRAY_BACKUP_SS_METHOD", "chacha20-ietf-poly1305").strip(),
                    "password": password,
                }
            ]
        },
    }


def _ensure_django() -> None:
    import sys
    import django
    if not os.environ.get("DJANGO_SETTINGS_MODULE"):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        for env_candidate in [Path(__file__).resolve().parent.parent / ".env"]:
            try:
                if env_candidate.is_file():
                    with open(env_candidate, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#") and "=" in line:
                                k, v = line.split("=", 1)
                                os.environ.setdefault(k.strip(), v.strip())
            except Exception:
                pass
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        if not Path("/.dockerenv").exists():
            if os.environ.get("POSTGRES_HOST") in (None, "db", "172.18.0.3"):
                os.environ["POSTGRES_HOST"] = "127.0.0.1"
        django.setup()


def get_free_port() -> int:
    """Finds an ephemeral free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def validate_config_format(url: str) -> tuple[bool, str, str]:
    """
    Validates if a link is a properly formatted proxy URL.
    Returns (is_valid: bool, protocol: str, error_msg: str).
    """
    url = html.unescape(url.strip())
    if not url:
        return False, "", "متن کانفیگ خالی است."

    scheme = url.split("://")[0].lower() if "://" in url else ""
    if scheme not in ("vless", "vmess", "trojan", "ss", "throne"):
        return False, "", f"پروتکل پشتیبانی‌نشده: «{scheme or 'نامشخص'}». فقط vless://, vmess://, trojan://, ss:// مجاز است."

    try:
        if scheme == "vmess":
            raw = url[8:]
            raw += "=" * ((4 - len(raw) % 4) % 4)
            data = json.loads(base64.b64decode(raw).decode("utf-8"))
            if not data.get("add") or not data.get("port") or not data.get("id"):
                return False, scheme, "کانفیگ VMess فاقد فیلدهای الزامی (add/port/id) است."
            return True, "vmess", ""

        parsed = urllib.parse.urlsplit(url)
        if not parsed.hostname and not parsed.netloc:
            return False, scheme, "آدرس سرور یا هاست کانفیگ نامعتبر است."

        return True, scheme, ""
    except Exception as exc:
        return False, scheme, f"خطا در تجزیه فرمت کانفیگ: {exc}"


def parse_vless_url(url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme != "vless":
        raise ValueError("Not a vless link")

    uuid = parsed.username
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

    stream_settings: dict[str, Any] = {
        "network": network,
        "security": security
    }

    if security == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": sni,
            "fingerprint": fp,
            "allowInsecure": False
        }
    elif security == "reality":
        stream_settings["realitySettings"] = {
            "serverName": sni,
            "fingerprint": fp,
            "show": False,
            "publicKey": params.get("pbk", [""])[0],
            "shortId": params.get("sid", [""])[0],
            "spiderX": params.get("spx", ["/"])[0]
        }

    if network == "ws":
        stream_settings["wsSettings"] = {
            "path": path,
            "headers": {"Host": ws_host}
        }
    elif network == "grpc":
        stream_settings["grpcSettings"] = {
            "serviceName": params.get("serviceName", [""])[0]
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
            "extra": extra
        }

    enc = params.get("encryption", ["none"])[0]
    flow = params.get("flow", [""])[0]
    user_obj: dict[str, Any] = {"id": uuid, "encryption": enc}
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
                    "users": [user_obj]
                }
            ]
        },
        "streamSettings": stream_settings
    }


def parse_vmess_url(url: str) -> dict[str, Any]:
    raw = url.strip()[8:]
    raw += "=" * ((4 - len(raw) % 4) % 4)
    data = json.loads(base64.b64decode(raw).decode("utf-8"))

    tag = data.get("ps", f"vmess_{data.get('add')}_{data.get('port')}")
    net = data.get("net", "tcp")
    sec = data.get("tls", "none")

    stream_settings: dict[str, Any] = {
        "network": net,
        "security": sec if sec in ["tls", "reality"] else "none"
    }
    if sec == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": data.get("sni") or data.get("host") or data.get("add"),
            "allowInsecure": False
        }
    if net == "ws":
        stream_settings["wsSettings"] = {
            "path": data.get("path", "/"),
            "headers": {"Host": data.get("host", data.get("add"))}
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
                            "security": "auto"
                        }
                    ]
                }
            ]
        },
        "streamSettings": stream_settings
    }


def parse_trojan_url(url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url.strip())
    password = parsed.username
    host = parsed.hostname
    port = parsed.port or 443
    params = urllib.parse.parse_qs(parsed.query)

    tag = urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"trojan_{host}_{port}"
    sec = params.get("security", ["tls"])[0]
    net = params.get("type", ["tcp"])[0]
    sni = params.get("sni", [host])[0]

    stream_settings: dict[str, Any] = {
        "network": net,
        "security": sec
    }
    if sec == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": sni,
            "allowInsecure": False
        }

    return {
        "tag": tag,
        "protocol": "trojan",
        "settings": {
            "servers": [
                {
                    "address": host,
                    "port": int(port),
                    "password": password
                }
            ]
        },
        "streamSettings": stream_settings
    }


def parse_ss_url(url: str) -> dict[str, Any]:
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
                    "password": password
                }
            ]
        }
    }


def parse_link_to_outbound(link: str) -> dict[str, Any]:
    link = html.unescape(link.strip())
    if link.startswith("vless://"):
        return parse_vless_url(link)
    elif link.startswith("vmess://"):
        return parse_vmess_url(link)
    elif link.startswith("trojan://"):
        return parse_trojan_url(link)
    elif link.startswith("ss://"):
        return parse_ss_url(link)
    raise ValueError(f"پروتکل پشتیبانی نشده: {link[:15]}...")


def is_valid_xray_outbound(ob: dict[str, Any]) -> tuple[bool, str]:
    """
    Validates outbound structure to ensure Xray core will not fail on startup.
    Rejects:
    - VLESS without TLS or encryption on public hosts
    - VLESS Reality missing publicKey
    - Missing server address/port or users
    """
    proto = ob.get("protocol")
    if proto == "vless":
        stream = ob.get("streamSettings", {})
        sec = stream.get("security", "none")
        vnext = ob.get("settings", {}).get("vnext", [])
        if not vnext:
            return False, "VLESS missing vnext servers"
        v0 = vnext[0]
        users = v0.get("users", [])
        if not users or not users[0].get("id"):
            return False, "VLESS missing user id"
        enc = users[0].get("encryption", "none")
        addr = v0.get("address", "")
        if sec in ("none", "") and enc in ("none", ""):
            is_local = (
                addr in ("127.0.0.1", "localhost", "::1")
                or addr.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.2", "172.3"))
            )
            if not is_local:
                return False, f"VLESS without TLS/encryption on public address ({addr}) is prohibited by Xray"
        if sec == "reality":
            r_settings = stream.get("realitySettings", {})
            if not r_settings.get("publicKey"):
                return False, "VLESS Reality requires non-empty publicKey"
    elif proto == "vmess":
        vnext = ob.get("settings", {}).get("vnext", [])
        if not vnext or not vnext[0].get("address") or not vnext[0].get("users"):
            return False, "VMess missing server or user configuration"
    elif proto in ("trojan", "shadowsocks"):
        servers = ob.get("settings", {}).get("servers", [])
        if not servers or not servers[0].get("address") or not servers[0].get("port"):
            return False, f"{proto} missing server address or port"
    return True, ""


def test_outbound_ping(outbound_dict: dict[str, Any], timeout_sec: float = 4.0) -> tuple[bool, float, str]:
    """
    Tests an outbound config using a temporary ephemeral Xray instance and python requests.
    If Xray binary is not on host, returns failure (no fake TCP socket fallback).
    Returns (is_connected: bool, ping_ms: float, error_message: str).
    """
    if not XRAY_BIN.exists():
        return False, -1.0, f"باینری Xray در سیستم یافت نشد ({XRAY_BIN})"

    test_port = get_free_port()
    test_cfg_path = Path(f"/tmp/xray_probe_{test_port}.json")

    test_cfg = {
        "log": {"loglevel": "error"},
        "inbounds": [
            {
                "tag": "test_socks",
                "listen": "127.0.0.1",
                "port": test_port,
                "protocol": "socks"
            }
        ],
        "outbounds": [
            outbound_dict,
            {"tag": "direct", "protocol": "freedom"}
        ]
    }

    try:
        with open(test_cfg_path, "w", encoding="utf-8") as f:
            json.dump(test_cfg, f)
    except Exception as e:
        return False, -1.0, f"خطا در ایجاد کانفیگ موقت: {e}"

    proc = None
    try:
        proc = subprocess.Popen(
            [str(XRAY_BIN), "-c", str(test_cfg_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        ready = False
        t_start = time.time()
        while time.time() - t_start < 1.8:
            try:
                with socket.create_connection(("127.0.0.1", test_port), timeout=0.1):
                    ready = True
                    break
            except (ConnectionRefusedError, socket.timeout):
                time.sleep(0.05)

        if not ready:
            return False, -1.0, "خطا: پروسه تست Xray آماده نشد."

        max_time = min(float(timeout_sec), 2.5)
        return probe_socks5_http(f"socks5h://127.0.0.1:{test_port}", probe_url=PROBE_URL, timeout_sec=max_time)
    except Exception as e:
        return False, -1.0, str(e)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=0.6)
            except Exception:
                proc.kill()
        if test_cfg_path.exists():
            try:
                test_cfg_path.unlink()
            except Exception:
                pass


def rebuild_xray_config_file() -> bool:
    """
    Regenerates xray/config.json with:
    - Default pool inbound on 1080 (SOCKS) & 8080 (HTTP)
    - Dedicated SOCKS inbounds on local_port (1081, 1082...) for each active ProxyConfig
    - Dedicated routing rules for each account's dedicated port
    """
    _ensure_django()
    from apps.discovery.models import ProxyConfig

    active_proxies = list(ProxyConfig.objects.filter(status="ACTIVE").exclude(local_port__isnull=True).order_by("local_port"))

    inbounds: list[dict[str, Any]] = [
        {
            "tag": "default_socks",
            "port": 1080,
            "listen": "0.0.0.0",
            "protocol": "socks",
            "settings": {"auth": "noauth", "udp": True}
        },
        {
            "tag": "default_http",
            "port": 8080,
            "listen": "0.0.0.0",
            "protocol": "http",
            "settings": {"allowTransparent": False}
        }
    ]

    outbounds: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []

    for p in active_proxies:
        try:
            ob = parse_link_to_outbound(p.raw_url)
            is_valid, invalid_reason = is_valid_xray_outbound(ob)
            if not is_valid:
                log.warning("Skipping invalid outbound for ProxyConfig #%s (%s): %s", p.id, p.tag, invalid_reason)
                p.status = "DEAD"
                p.save(update_fields=["status"])
                continue

            port_tag = f"proxy_{p.local_port}"
            in_tag = f"in_{p.local_port}"
            ob["tag"] = port_tag

            # Dedicated inbound
            inbounds.append({
                "tag": in_tag,
                "port": int(p.local_port),
                "listen": "0.0.0.0",
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True}
            })
            outbounds.append(ob)
            # Route: in_1081 -> proxy_1081
            rules.append({
                "type": "field",
                "inboundTag": [in_tag],
                "outboundTag": port_tag
            })
        except Exception as exc:
            log.warning("Could not parse outbound for ProxyConfig #%s: %s", p.id, exc)

    # No healthy proxy in the DB: never replace a hand-maintained config that still has a real upstream outbound
    # (doing so used to leave Xray with only "direct" and cut every scraper/Telegram connection).
    if not outbounds and CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if any(o.get("protocol") not in {"freedom", "blackhole", "dns"} for o in existing.get("outbounds", [])):
                log.warning("No ACTIVE proxies in DB; keeping the existing Xray config (it has its own upstream outbound)")
                return True
        except Exception as exc:
            log.warning("Could not inspect existing Xray config: %s", exc)

    # Fallback default outbound
    if outbounds:
        default_ob = dict(outbounds[0])
        default_ob["tag"] = "default_proxy"
        outbounds.insert(0, default_ob)
    else:
        # DB has 0 active proxies: inject the permanent fallback tunnel as default_proxy
        # so Xray and the Telegram bot never lose outbound connectivity to filtered services.
        outbounds.insert(0, backup_tunnel())

    # Tags must be unique or Xray refuses to start ("existing tag found")
    if not any(o.get("tag") == "direct" for o in outbounds):
        outbounds.append({"tag": "direct", "protocol": "freedom"})

    full_config = {
        "log": {"loglevel": "warning"},
        "inbounds": inbounds,
        "outbounds": outbounds,
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": rules
        }
    }

    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        if CONFIG_FILE.exists():   # keep the previous config (last 10) so a bad regeneration is always reversible
            import shutil
            stamp = time.strftime("%Y%m%dT%H%M%S")
            shutil.copy2(CONFIG_FILE, CONFIG_FILE.with_name(f"{CONFIG_FILE.name}.bak-{stamp}"))
            for old_bak in sorted(CONFIG_FILE.parent.glob(f"{CONFIG_FILE.name}.bak-*"))[:-10]:
                old_bak.unlink(missing_ok=True)
        tmp_path = CONFIG_FILE.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(full_config, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, CONFIG_FILE)
        log.info("Regenerated Xray config with %d dedicated proxy ports", len(active_proxies))
        return True
    except Exception as exc:
        log.error("Failed to write %s: %s", CONFIG_FILE, exc)
        return False


def reload_xray_service() -> bool:
    """Restarts the Xray container when the docker CLI is available (host scripts).

    Inside the app containers there is no docker CLI: the xray service (docker-compose, xray/watch.sh) restarts
    itself when config.json changes (at most every XRAY_RELOAD_MIN_SECONDS), so new dedicated ports go live without a
    manual restart.
    """
    try:
        res = subprocess.run(
            ["docker", "restart", "moshtari-yab-xray-1"],
            capture_output=True,
            text=True,
            timeout=15
        )
        return res.returncode == 0
    except FileNotFoundError:   # app container: the xray service reloads itself (xray/watch.sh)
        log.debug("no docker CLI here; the xray service picks up config.json changes by itself via watch.sh")
        return False
    except Exception as exc:
        log.warning("Could not restart Xray container: %s", exc)
        return False


def get_next_available_local_port() -> int:
    """Finds the lowest free port starting from BASE_LOCAL_PORT (1081)."""
    _ensure_django()
    from apps.discovery.models import ProxyConfig

    used_ports = set(ProxyConfig.objects.exclude(local_port__isnull=True).values_list("local_port", flat=True))
    port = BASE_LOCAL_PORT
    while port in used_ports:
        port += 1
    return port


def add_proxy_config(raw_link: str, tag_prefix: str = "") -> dict[str, Any]:
    """
    Validates, tests latency, and if alive, saves to DB and assigns dedicated port.
    If ping == -1 / dead, does not save and returns failure.
    """
    _ensure_django()
    from apps.discovery.models import ProxyConfig
    from django.utils import timezone

    raw_link = html.unescape(raw_link.strip())
    valid, proto, err = validate_config_format(raw_link)
    if not valid:
        return {"success": False, "ping_ms": -1.0, "message": f"فرمت کانفیگ نامعتبر است: {err}"}

    try:
        ob = parse_link_to_outbound(raw_link)
    except Exception as exc:
        return {"success": False, "ping_ms": -1.0, "message": f"خطا در تجزیه ساختار کانفیگ: {exc}"}

    # Live test ping (max 2.5s timeout; ping > 2000ms is treated as -1)
    ok, ping_ms, test_err = test_outbound_ping(ob, timeout_sec=2.5)   # fails clearly when the xray binary is missing
    if not ok or ping_ms < 0 or ping_ms > 2000:
        # If config already existed in DB, delete it
        if ProxyConfig.objects.filter(raw_url=raw_link).exists():
            ProxyConfig.objects.filter(raw_url=raw_link).delete()
            rebuild_xray_config_file()
            reload_xray_service()
        return {
            "success": False,
            "ping_ms": -1.0,
            "protocol": proto,
            "tag": ob.get("tag", "بدون نام"),
            "message": f"[ناموفق] تست پینگ ناموفق بود (پینگ -۱ یا بالای ۲۰۰۰ms). {test_err or 'تایم‌اوت / قطعی'}. کانفیگ اضافه نگردید."
        }

    # Extract tag, server, port
    tag = ob.get("tag") or f"{proto}_{int(time.time())}"
    if tag_prefix and not tag.startswith(tag_prefix):
        tag = f"{tag_prefix}_{tag}"
    tag = tag[:145]
    server = ""
    port = 443
    if proto in ("vless", "vmess"):
        vnext = ob.get("settings", {}).get("vnext", [])
        if vnext:
            server = vnext[0].get("address", "")
            port = vnext[0].get("port", 443)
    elif proto in ("trojan", "shadowsocks"):
        servers = ob.get("settings", {}).get("servers", [])
        if servers:
            server = servers[0].get("address", "")
            port = servers[0].get("port", 443)

    obj, created = ProxyConfig.objects.get_or_create(
        raw_url=raw_link,
        defaults={
            "protocol": proto,
            "tag": tag,
            "server": server,
            "port": port,
            "local_port": get_next_available_local_port(),
            "ping_ms": ping_ms,
            "status": "ACTIVE",
            "last_checked_at": timezone.now()
        }
    )

    if not created:
        if not obj.local_port:
            obj.local_port = get_next_available_local_port()
        obj.ping_ms = ping_ms
        obj.status = "ACTIVE"
        obj.last_checked_at = timezone.now()
        obj.save()

    # Rebuild Xray config and reload
    rebuild_xray_config_file()
    reload_xray_service()

    # Auto assign to any unassigned account
    from apps.discovery.proxies.assignment import assign_proxies_to_accounts
    assign_proxies_to_accounts()

    return {
        "success": True,
        "ping_ms": ping_ms,
        "protocol": proto,
        "tag": tag,
        "local_port": obj.local_port,
        "proxy_url": f"socks5h://xray:{obj.local_port}",
        "message": f"[موفق] کانفیگ سالم است و متصل شد. پینگ: {ping_ms}ms | پورت اختصاصی: {obj.local_port}"
    }


