"""Mobile 4G & Residential Proxy Intelligence and Automated IP Rotation Service.

Protects Instagram Sender accounts from Meta's strict Anti-Abuse Risk Engine:
1. Deep IP Egress & ISP Inspection (Detects whether an IP is 4G Mobile, Residential, or Datacenter).
2. Automated 4G Dongle & Mobile Proxy IP Rotation (via Rotation Webhook / Link).
3. Pre-Flight Sender Proxy Validation (Warns on high-risk datacenter IPs before DMs are dispatched).
4. Meta / Instagram Domain Reachability Verification.
"""
from __future__ import annotations

import logging
import re
import socket
import time
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

from django.utils import timezone

logger = logging.getLogger("instagram.proxy")

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


def _get_http_client():
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
        http_requests = _get_http_client()
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


def rotate_mobile_ip(rotation_url: str, post_rotation_delay: float = 5.0, timeout: float = 12.0) -> Dict[str, Any]:
    """
    Triggers an IP rotation on a 4G LTE Mobile Proxy dongle or API endpoint.
    Pauses post_rotation_delay seconds to allow the cellular connection to re-establish.
    """
    if not rotation_url:
        return {"success": False, "error": "No rotation URL provided."}

    logger.info("Triggering 4G Mobile IP rotation via: %s", rotation_url)
    try:
        http_requests = _get_http_client()
        # Direct call to the rotation webhook (without using the proxy itself)
        resp = http_requests.get(rotation_url, timeout=timeout)
        time.sleep(post_rotation_delay)
        return {
            "success": 200 <= resp.status_code < 300,
            "status_code": resp.status_code,
            "response": resp.text[:200],
            "error": None,
        }
    except Exception as exc:
        logger.warning("Mobile IP rotation failed for %s: %s", rotation_url, exc)
        return {"success": False, "status_code": 0, "response": "", "error": str(exc)}


def rotate_account_proxy_ip(account, post_delay: float = 5.0) -> Dict[str, Any]:
    """
    Rotates the mobile IP for a specific InstagramEngineAccount if a rotation URL is configured.
    Updates the account's internal proxy metadata with the new IP and ISP.
    """
    rotation_url = extract_rotation_url(account)
    if not rotation_url:
        return {"rotated": False, "reason": "No rotation URL configured for this account."}

    rot_res = rotate_mobile_ip(rotation_url, post_rotation_delay=post_delay)
    if not rot_res.get("success"):
        return {"rotated": False, "reason": f"Rotation API failed: {rot_res.get('error')}"}

    # Inspect the newly assigned IP
    clean_url = clean_proxy_url(account.proxy_url)
    inspection = inspect_proxy_egress(clean_url)

    # Save to account proxy_meta inside device_settings JSON
    ds = account.device_settings or {}
    if not isinstance(ds, dict):
        ds = {}
    pm = ds.get("proxy_meta") or {}
    pm["rotation_url"] = rotation_url
    pm["last_rotated_at"] = timezone.now().isoformat()
    pm["last_ip"] = inspection.get("ip", "")
    pm["isp"] = inspection.get("isp", "")
    pm["classification"] = inspection.get("classification", "MOBILE_4G")
    pm["risk_score"] = inspection.get("risk_score", "SAFE")
    ds["proxy_meta"] = pm
    account.device_settings = ds
    account.save(update_fields=["device_settings"])

    logger.info(
        "Successfully rotated IP for @%s -> New IP: %s (%s, %s)",
        account.username, inspection.get("ip"), inspection.get("isp"), inspection.get("classification")
    )
    return {
        "rotated": True,
        "new_ip": inspection.get("ip"),
        "isp": inspection.get("isp"),
        "classification": inspection.get("classification"),
        "risk_score": inspection.get("risk_score"),
    }


def prepare_sender_proxy_before_dm(account, stdout=None, rotate: Optional[bool] = None) -> Dict[str, Any]:
    """
    Pre-Flight guardrail executed before dispatching Instagram DMs:
    1. Inspects current IP and verifies ISP risk level, printing a human-readable status to stdout.
    2. Rotates the 4G IP only when asked (rotate=True or INSTAGRAM_PROXY_ROTATE_EACH_DM). A real phone keeps its
       IP for hours; a logged-in session that shows up from a new IP before every DM looks automated. After a
       proxy error outreach_engine.apply_sender_error rotates the IP and rests the sender instead.
    """
    from django.conf import settings

    rotation_url = extract_rotation_url(account)
    rotated = False
    info: Dict[str, Any] = {}
    if rotate is None:
        rotate = bool(getattr(settings, "INSTAGRAM_PROXY_ROTATE_EACH_DM", False))

    if rotation_url and rotate:
        if stdout:
            stdout.write("   🔄 [پروکسی موبایل 4G]: چرخش خودکار IP جهت دریافت آی‌پی تازه سلولار...")
        res = rotate_account_proxy_ip(account, post_delay=4.5)
        rotated = res.get("rotated", False)
        # If rotation succeeded, reuse the inspection metadata already captured during rotation!
        # Avoids hitting ip-api.com twice per DM and saves 5-10s of network latency.
        if rotated and res.get("new_ip"):
            info = {
                "ip": res.get("new_ip", ""),
                "isp": res.get("isp", ""),
                "classification": res.get("classification", "MOBILE_4G"),
                "risk_score": res.get("risk_score", "SAFE"),
            }

    # Only inspect external egress IP if we haven't already captured it via rotation
    if not info:
        # Instagram accounts: the connection their clients really use (own proxy / config / shared pool)
        clean_url = account.clean_proxy if hasattr(account, "clean_proxy") else clean_proxy_url(account.proxy_url)
        info = inspect_proxy_egress(clean_url)

    ip_str = info.get("ip", "نامشخص")
    isp_str = info.get("isp", "نامشخص")
    classification = info.get("classification", "UNKNOWN")
    risk_score = info.get("risk_score", "MODERATE")

    if stdout:
        if classification == "MOBILE_4G":
            stdout.write(f"   📶 [شبکه موبایل ۴G/LTE]: آی‌پی: {ip_str} | اپراتور: {isp_str} (سطح اعتماد متا: حداکثر ✅)")
        elif classification == "RESIDENTIAL":
            stdout.write(f"   🏠 [اینترنت مسکونی/خانگی]: آی‌پی: {ip_str} | ارائه‌دهنده: {isp_str} (ریسک: پایین ✅)")
        elif classification == "DATACENTER":
            stdout.write(f"   ⚠️ [هشدار پروکسی دیتاسنتری]: آی‌پی: {ip_str} ({isp_str}) در رنج هاستینگ است. برای ماندگاری اکانت‌های فرستنده استفاده از پروکسی ۴G پیشنهاد می‌شود.")

    return {
        "rotated": rotated,
        "egress_ip": ip_str,
        "isp": isp_str,
        "classification": classification,
        "risk_score": risk_score,
    }
