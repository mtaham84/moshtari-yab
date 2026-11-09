"""Is a proxy usable: TCP reachability, real tunnel probes of V2Ray configs, and the egress IP's network type."""
from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Any, Dict
from urllib.parse import urlparse

from apps.discovery.proxies.urls import RESERVED_PORTS, XRAY_HOST, clean_proxy_url

logger = logging.getLogger("instagram.proxy")

MAX_PING_MS = 2000.0   # a config slower than this counts as dead

# Known Cellular & Mobile Carrier Keywords in ISP/Org Names
MOBILE_CARRIER_KEYWORDS = [
    "mobile", "cellular", "mci", "irancell", "rightel", "telecom", "vodafone",
    "t-mobile", "verizon", "att", "at&t", "orange", "o2", "telefonica", "claro",
    "tim", "ee", "three", "movistar", "airtel", "jio", "zain", "turkcell",
]

# Known Hosting / Datacenter Providers
DATACENTER_KEYWORDS = [
    "hetzner", "ovh", "digitalocean", "linode", "amazon", "aws", "google",
    "azure", "microsoft", "vultr", "greenhost", "contabo", "leaseweb",
    "cloudflare", "fastly", "akamai", "alibaba", "oracle", "choopa", "m247",
]


def http_client():
    """
    Returns an HTTP client module: curl_cffi.requests (for TLS fingerprint evasion)
    with seamless fallback to standard requests if curl_cffi is not installed.
    """
    try:
        from curl_cffi import requests
        return requests
    except ImportError:
        import requests
        return requests



def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        if host == "xray" and not Path("/.dockerenv").exists():
            try:
                with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
                    return True
            except (OSError, ValueError):
                pass
        return False


def proxy_reachable(url, timeout: float = 4.0) -> tuple[bool, str]:
    """TCP check of the proxy host:port (the Instagram check itself tells whether the upstream works)."""
    url = clean_proxy_url(url)
    if not url:
        return True, "بدون پروکسی (IP سرور)"
    try:
        parsed = urlparse(url)
        host, port = parsed.hostname, parsed.port or (1080 if "socks" in (parsed.scheme or "") else 8080)
    except ValueError:
        return False, "فرمت پروکسی نامعتبر است"
    if not host:
        return False, "فرمت پروکسی نامعتبر است"
    if port_open(host, port, timeout):
        return True, f"پروکسی {host}:{port} در دسترس است"
    return False, f"پروکسی {host}:{port} در دسترس نیست"


def config_live(config) -> bool:
    """The config is ACTIVE and xray really listens on its port (new configs only go live after xray reloads)."""
    return bool(config and config.status == "ACTIVE" and config.local_port
                and config.local_port not in RESERVED_PORTS and port_open(XRAY_HOST, config.local_port))


def measure_config(config, timeout_sec: float = 2.5) -> tuple[bool, float, str]:
    """Real tunnel probe of a ProxyConfig: through its xray port, or through an ephemeral xray when it has no port yet.
    Returns (live, ping_ms, error); live means it answered within MAX_PING_MS. Nothing is saved."""
    from xray import multi_proxy_manager as xray

    try:
        if config.local_port:
            ok, ping_ms, err = xray.probe_proxy_port(config.local_port, timeout_sec=timeout_sec)
        elif config.raw_url:
            ok, ping_ms, err = xray.test_outbound_ping(xray.parse_link_to_outbound(config.raw_url),
                                                       timeout_sec=timeout_sec)
        else:
            return False, -1.0, "no port and no link"
    except Exception as exc:
        logger.warning("Error probing config #%s: %s", config.pk, exc)
        return False, -1.0, str(exc)
    if ok and 0 < ping_ms <= MAX_PING_MS:
        return True, round(ping_ms, 1), ""
    return False, -1.0, err


def inspect_proxy_egress(proxy_url: str, timeout: float = 6.0) -> Dict[str, Any]:
    """
    Connects through the proxy to inspect the public egress IP, ISP, country, and risk level.
    Uses curl_cffi or requests with fallback.
    """
    clean_url = clean_proxy_url(proxy_url)
    if not clean_url:
        return {
            "status": "direct",
            "ip": "direct",
            "isp": "Direct Connection",
            "country": "Local",
            "is_mobile": False,
            "is_datacenter": True,
            "classification": "DIRECT",
            "risk_score": "HIGH_RISK",
        }

    try:
        http_requests = http_client()
        proxies = {"http": clean_url, "https": clean_url}
        resp = http_requests.get(
            "http://ip-api.com/json?fields=status,message,country,countryCode,city,isp,org,as,query,mobile,proxy,hosting",
            proxies=proxies,
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "success":
                isp = str(data.get("isp") or "").lower()
                org = str(data.get("org") or "").lower()
                as_name = str(data.get("as") or "").lower()
                all_text = f"{isp} {org} {as_name}"

                api_mobile = bool(data.get("mobile", False))
                api_hosting = bool(data.get("hosting", False))

                # Heuristic detection for Mobile 4G/LTE
                is_mobile = api_mobile or any(k in all_text for k in MOBILE_CARRIER_KEYWORDS)
                is_dc = api_hosting or any(k in all_text for k in DATACENTER_KEYWORDS)

                if is_mobile:
                    classification = "MOBILE_4G"
                    risk = "SAFE"
                elif not is_dc:
                    classification = "RESIDENTIAL"
                    risk = "LOW"
                else:
                    classification = "DATACENTER"
                    risk = "HIGH_RISK"

                return {
                    "status": "success",
                    "ip": data.get("query", ""),
                    "isp": data.get("isp", ""),
                    "org": data.get("org", ""),
                    "country": data.get("country", ""),
                    "country_code": data.get("countryCode", ""),
                    "city": data.get("city", ""),
                    "is_mobile": is_mobile,
                    "is_datacenter": is_dc,
                    "classification": classification,
                    "risk_score": risk,
                }
    except Exception as exc:
        logger.debug("Proxy egress inspection error via %s: %s", clean_url, exc)

    return {
        "status": "error",
        "ip": "unknown",
        "isp": "unknown",
        "country": "unknown",
        "is_mobile": False,
        "is_datacenter": False,
        "classification": "UNKNOWN",
        "risk_score": "MODERATE",
    }
