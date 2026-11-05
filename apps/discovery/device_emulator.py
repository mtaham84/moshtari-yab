"""Instagram Device Emulation & Hardware Fingerprint Generator.

Assigns realistic Android flagship device profiles (Pixel 8 Pro, Galaxy S24 Ultra, Xiaomi 14 Pro, etc.)
with unique Android IDs, UUIDs, and hardware capabilities to each Instagram account so that
Instagram servers see completely distinct physical devices and network endpoints.
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from typing import Any
import uuid

log = logging.getLogger("instagram.device")

# Realistic Modern Flagship Devices (Android 13 / 14)
DEVICE_CATALOG: list[dict[str, Any]] = [
    {
        "name": "Google Pixel 8 Pro",
        "device_settings": {
            "android_version": 34,
            "android_release": "14",
            "dpi": "480dpi",
            "resolution": "1344x2992",
            "manufacturer": "Google/google",
            "device": "husky",
            "model": "Pixel 8 Pro",
            "cpu": "husky",
            "app_version": "449.0.0.52.84",
            "version_code": "1079242191",
            "bloks_versioning_id": "799db3e387d7a21a19c72918b8698b6cd28a1516ade363792c100ed45a0f9f0b"
        }
    },
    {
        "name": "Samsung Galaxy S24 Ultra",
        "device_settings": {
            "android_version": 34,
            "android_release": "14",
            "dpi": "500dpi",
            "resolution": "1440x3120",
            "manufacturer": "Samsung",
            "device": "e3q",
            "model": "SM-S928B",
            "cpu": "qcom",
            "app_version": "449.0.0.52.84",
            "version_code": "1079242191",
            "bloks_versioning_id": "799db3e387d7a21a19c72918b8698b6cd28a1516ade363792c100ed45a0f9f0b"
        }
    },
    {
        "name": "Xiaomi 14 Pro",
        "device_settings": {
            "android_version": 34,
            "android_release": "14",
            "dpi": "520dpi",
            "resolution": "1440x3200",
            "manufacturer": "Xiaomi",
            "device": "shennong",
            "model": "23116PN5BC",
            "cpu": "qcom",
            "app_version": "449.0.0.52.84",
            "version_code": "1079242191",
            "bloks_versioning_id": "799db3e387d7a21a19c72918b8698b6cd28a1516ade363792c100ed45a0f9f0b"
        }
    },
    {
        "name": "OnePlus 12",
        "device_settings": {
            "android_version": 34,
            "android_release": "14",
            "dpi": "510dpi",
            "resolution": "1440x3168",
            "manufacturer": "OnePlus",
            "device": "OP595DL",
            "model": "CPH2581",
            "cpu": "qcom",
            "app_version": "449.0.0.52.84",
            "version_code": "1079242191",
            "bloks_versioning_id": "799db3e387d7a21a19c72918b8698b6cd28a1516ade363792c100ed45a0f9f0b"
        }
    },
    {
        "name": "Samsung Galaxy S23+",
        "device_settings": {
            "android_version": 33,
            "android_release": "13",
            "dpi": "450dpi",
            "resolution": "1080x2340",
            "manufacturer": "Samsung",
            "device": "dm2q",
            "model": "SM-S916B",
            "cpu": "qcom",
            "app_version": "449.0.0.52.84",
            "version_code": "1079242191",
            "bloks_versioning_id": "799db3e387d7a21a19c72918b8698b6cd28a1516ade363792c100ed45a0f9f0b"
        }
    },
    {
        "name": "Google Pixel 7 Pro",
        "device_settings": {
            "android_version": 33,
            "android_release": "13",
            "dpi": "480dpi",
            "resolution": "1440x3120",
            "manufacturer": "Google/google",
            "device": "cheetah",
            "model": "Pixel 7 Pro",
            "cpu": "cheetah",
            "app_version": "449.0.0.52.84",
            "version_code": "1079242191",
            "bloks_versioning_id": "799db3e387d7a21a19c72918b8698b6cd28a1516ade363792c100ed45a0f9f0b"
        }
    },
    {
        "name": "Sony Xperia 1 V",
        "device_settings": {
            "android_version": 34,
            "android_release": "14",
            "dpi": "640dpi",
            "resolution": "1644x3840",
            "manufacturer": "Sony",
            "device": "pdx234",
            "model": "XQ-DQ72",
            "cpu": "qcom",
            "app_version": "449.0.0.52.84",
            "version_code": "1079242191",
            "bloks_versioning_id": "799db3e387d7a21a19c72918b8698b6cd28a1516ade363792c100ed45a0f9f0b"
        }
    }
]


def generate_unique_device_profile(identifier: str | int) -> dict[str, Any]:
    """Generates a complete, unique device configuration for a specific account."""
    # Pick device from catalog deterministically using identifier hash
    hash_val = int(hashlib.md5(str(identifier).encode("utf-8")).hexdigest(), 16)
    template = DEVICE_CATALOG[hash_val % len(DEVICE_CATALOG)]

    android_device_id = f"android-{secrets.token_hex(8)}"
    phone_id = str(uuid.uuid4())
    client_uuid = str(uuid.uuid4())
    client_session_id = str(uuid.uuid4())
    advertising_id = str(uuid.uuid4())
    device_id = f"android-{secrets.token_hex(8)}"

    profile = {
        "device_name": template["name"],
        "device_settings": dict(template["device_settings"]),
        "uuids": {
            "phone_id": phone_id,
            "uuid": client_uuid,
            "client_session_id": client_session_id,
            "advertising_id": advertising_id,
            "android_device_id": android_device_id,
            "device_id": device_id,
        }
    }
    return profile


# Keys written while a login is waiting for a verification code; they never describe the phone itself.
_PENDING_LOGIN_KEYS = ("settings", "last_json", "challenge_hint")
_LOCALE = {"locale": "fa_IR", "country": "IR", "country_code": 98, "timezone_offset": 12600}


def has_device_profile(data) -> bool:
    """True when `data` names one concrete phone: hardware settings plus its UUIDs."""
    if not isinstance(data, dict):
        return False
    dev, uuids = data.get("device_settings"), data.get("uuids")
    return (isinstance(dev, dict) and bool(dev.get("model"))
            and isinstance(uuids, dict) and bool(uuids.get("uuid")) and bool(uuids.get("phone_id")))


def _device_name(dev: dict) -> str:
    for item in DEVICE_CATALOG:
        if item["device_settings"].get("model") == dev.get("model"):
            return item["name"]
    return f"{str(dev.get('manufacturer') or '').split('/')[0].strip().title()} {dev.get('model') or ''}".strip()


def profile_from_client_settings(settings) -> dict[str, Any] | None:
    """The phone stored in instagrapi settings (session_json / get_settings()), i.e. what Instagram already saw."""
    if not has_device_profile(settings):
        return None
    dev = dict(settings["device_settings"])
    return {"device_name": _device_name(dev), "device_settings": dev, "uuids": dict(settings["uuids"])}


def resolve_device_settings(current, session_json=None, identifier: str | int = "default") -> dict[str, Any]:
    """`current` with a device profile, never a new random phone when one is already known.

    Order: the profile already in `current` -> the phone of a pending login (`current["settings"]`) -> the phone of the
    saved session -> a brand new profile (first time only). Other keys such as proxy_meta are kept.
    """
    ds = dict(current) if isinstance(current, dict) else {}
    if has_device_profile(ds):
        return ds
    profile = (profile_from_client_settings(ds.get("settings")) or profile_from_client_settings(session_json)
               or generate_unique_device_profile(identifier))
    return {**ds, **profile}


def account_device_settings(account, client_settings=None, **extra) -> dict[str, Any]:
    """device_settings to store on `account`: its one phone (model + UUIDs) plus proxy_meta, never a new device.

    client_settings: cl.get_settings() of the client that just talked to Instagram; its phone becomes the profile
    because that is the device Instagram now associates with the account. `extra` adds pending-login keys
    (settings / last_json / challenge_hint); without them those keys are dropped.
    """
    cur = account.device_settings if isinstance(account.device_settings, dict) else {}
    ds = {k: v for k, v in cur.items() if k not in _PENDING_LOGIN_KEYS}
    ds.update(profile_from_client_settings(client_settings) or {})
    ds = resolve_device_settings(ds, account.session_json, account.username or getattr(account, "pk", None) or "default")
    ds.update({k: v for k, v in extra.items() if v is not None})
    return ds


def ensure_account_device_profile(account) -> dict[str, Any]:
    """Ensures the Instagram account has an assigned device profile in device_settings (reusing a known phone)."""
    cur = account.device_settings
    if has_device_profile(cur):
        return cur

    account.device_settings = resolve_device_settings(cur, account.session_json,
                                                      account.username or getattr(account, "id", None) or "default")
    if getattr(account, "pk", None):
        account.save(update_fields=["device_settings"])
    else:
        account.save()
    log.info("Assigned device profile '%s' to Instagram account @%s",
             account.device_settings.get("device_name"), account.username)
    return account.device_settings


def _apply_locale(cl) -> None:
    for setter, value in (("set_locale", _LOCALE["locale"]), ("set_country", _LOCALE["country"]),
                          ("set_country_code", _LOCALE["country_code"]),
                          ("set_timezone_offset", _LOCALE["timezone_offset"])):
        try:
            getattr(cl, setter)(value)
        except Exception as exc:
            log.debug("%s failed: %s", setter, exc)


def apply_device_profile_to_client(cl, account) -> None:
    """Applies the persistent device settings and UUIDs to the instagrapi Client.

    The device is also written to cl.settings: instagrapi re-reads cl.settings on login_by_sessionid() (Client.init),
    and older instagrapi releases would otherwise fall back to the library's default phone there.
    """
    profile = ensure_account_device_profile(account)
    device_settings = dict(profile.get("device_settings") or {})
    uuids = dict(profile.get("uuids") or {})

    if uuids:
        try:
            cl.set_uuids(uuids)
        except Exception as exc:
            log.warning("Could not set_uuids on Client: %s", exc)
    try:
        cl.set_device(device_settings, reset=False)
        cl.settings["device_settings"] = dict(cl.device_settings)
    except Exception as exc:
        log.warning("Could not set_device on Client: %s", exc)
    _apply_locale(cl)
    try:
        cl.set_user_agent()
        cl.private.headers.update(cl.base_headers)
    except Exception:
        pass


def connect_saved_session(cl, account) -> bool:
    """Authenticates `cl` from the account's saved session on the same phone that created it.

    Full instagrapi settings (UUIDs + device + cookies + Authorization) are restored with set_settings(): no new
    login request, no device change. Only a bare sessionid (older rows) falls back to the account's device profile
    plus login_by_sessionid(). Returns True when a login request was made. Set the proxy before calling.
    """
    sj = account.session_json if isinstance(account.session_json, dict) else {}
    sessionid = (sj.get("cookies") or {}).get("sessionid") or (sj.get("authorization_data") or {}).get("sessionid")
    if not sessionid:
        raise RuntimeError(f"login_required: no sessionid saved for @{account.username}")
    if has_device_profile(sj):
        cl.set_settings(sj)  # keeps the session's own locale/timezone too: nothing about the phone changes
        if not getattr(cl, "username", None):
            cl.username = account.username
        return False
    apply_device_profile_to_client(cl, account)
    cl.login_by_sessionid(sessionid)
    return True
