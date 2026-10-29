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


def ensure_account_device_profile(account) -> dict[str, Any]:
    """Ensures the Instagram account has an assigned device profile in device_settings."""
    cur = account.device_settings or {}
    if isinstance(cur, dict) and cur.get("device_settings") and cur.get("uuids"):
        return cur

    profile = generate_unique_device_profile(account.username or account.id)
    account.device_settings = profile
    account.save(update_fields=["device_settings"])
    log.info("Assigned device profile '%s' to Instagram account @%s", profile["device_name"], account.username)
    return profile


def apply_device_profile_to_client(cl, account) -> None:
    """Applies the persistent device settings and UUIDs to the instagrapi Client."""
    profile = ensure_account_device_profile(account)
    device_settings = profile.get("device_settings") or {}
    uuids = profile.get("uuids") or {}

    try:
        cl.set_device(device_settings, reset=False)
    except Exception as exc:
        log.warning("Could not set_device on Client: %s", exc)

    if uuids:
        try:
            cl.set_uuids(uuids)
            if "android_device_id" in uuids:
                cl.android_device_id = uuids["android_device_id"]
        except Exception as exc:
            log.warning("Could not set_uuids on Client: %s", exc)

    try:
        cl.set_user_agent()
    except Exception:
        pass
