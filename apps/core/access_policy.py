"""Core Access Policy & Tenant Isolation Contract.

Guarantees:
1. Shared platform sender accounts are allowed (PLATFORM_TENANT_ID = 'platform').
2. Tenant data isolation is absolute: Tenant A can never view, mutate, or followup Tenant B's leads.
3. Pitch templates for shared accounts are isolated per-tenant.
4. Only superusers/system admins can manage or enroll accounts into the platform pool.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from django.core.exceptions import PermissionDenied

log = logging.getLogger(__name__)

PLATFORM_TENANT_ID = "platform"
CONFIG_PATH = Path("data/sarv_outreach_config.json")


class AccessPolicy:
    """Central authority for tenant boundaries, platform account pool, and permissions."""

    PLATFORM_TENANT_ID = PLATFORM_TENANT_ID
    CONFIG_PATH = CONFIG_PATH

    @staticmethod
    def actor_tenant_key(user: Any) -> str | None:
        """Returns the primary tenant key for the user's business."""
        if not user or not user.is_authenticated:
            return None
        business = getattr(user, "business", None)
        if not business:
            return None
        return f"tenant_{business.id}"

    @classmethod
    def actor_tenant_keys(cls, user: Any) -> list[str]:
        """Returns all valid tenant keys the user owns data for.
        Includes legacy alias 'sarv' for business 4.
        """
        key = cls.actor_tenant_key(user)
        if not key:
            return []
        keys = [key]
        business = getattr(user, "business", None)
        if business and business.id == 4:
            keys.append("sarv")
        return keys

    @classmethod
    def sender_search_tenants(cls, user_or_tenant: Any) -> list[str]:
        """Returns the tenant keys allowed when selecting SENDER accounts.
        Always includes the tenant's own keys PLUS the shared platform pool.
        """
        if isinstance(user_or_tenant, str):
            tenants = [user_or_tenant]
            if user_or_tenant in ("tenant_4", "sarv"):
                tenants = ["tenant_4", "sarv"]
        else:
            tenants = cls.actor_tenant_keys(user_or_tenant)

        if PLATFORM_TENANT_ID not in tenants:
            tenants.append(PLATFORM_TENANT_ID)
        return tenants

    @classmethod
    def can_view_or_modify_lead(cls, user: Any, lead: Any) -> bool:
        """Checks if user has permission to access or message a lead.
        Absolute tenant boundary: Tenant A can never access Tenant B's leads,
        even if both use the same shared platform sender.
        """
        if not user or not user.is_authenticated:
            return False
        if getattr(user, "is_superuser", False):
            return True
        if not lead:
            return False

        lead_tenant = getattr(lead, "tenant_id", None)
        if not lead_tenant:
            return False

        allowed_tenants = cls.actor_tenant_keys(user)
        return lead_tenant in allowed_tenants

    @classmethod
    def assert_can_access_lead(cls, user: Any, lead: Any) -> None:
        """Raises PermissionDenied (HTTP 403) if user is not authorized for this lead."""
        if not cls.can_view_or_modify_lead(user, lead):
            raise PermissionDenied("دسترسی غیرمجاز: این لید متعلق به کسب‌وکار دیگری است.")

    @classmethod
    def can_manage_sender_account(cls, user: Any, account: Any) -> bool:
        """Determines if user can edit credentials, settings, or clear cooldown for an account.
        - Platform pool accounts can ONLY be managed by superusers.
        - Dedicated accounts can be managed by the tenant owner or superusers.
        """
        if not user or not user.is_authenticated:
            return False
        if getattr(user, "is_superuser", False):
            return True
        if not account:
            return False

        account_tenant = getattr(account, "tenant_id", "")
        # Platform pool account: strictly admin-only
        if account_tenant == PLATFORM_TENANT_ID:
            return False

        allowed_tenants = cls.actor_tenant_keys(user)
        return account_tenant in allowed_tenants

    @classmethod
    def assert_can_manage_account(cls, user: Any, account: Any) -> None:
        """Raises PermissionDenied (HTTP 403) if user cannot manage this sender account."""
        if not cls.can_manage_sender_account(user, account):
            raise PermissionDenied("دسترسی غیرمجاز به مدیریت این اکانت فرستنده.")

    @classmethod
    def can_enroll_in_platform_pool(cls, user: Any, target_tenant: str) -> bool:
        """Only superusers can enroll or reassign an account into the platform pool."""
        if target_tenant == PLATFORM_TENANT_ID:
            return bool(user and user.is_authenticated and getattr(user, "is_superuser", False))
        return True

    @classmethod
    def is_sender_available(cls, sender: Any) -> bool:
        """Checks if a sender account is currently operational and not in cooldown."""
        if not sender:
            return False
        if getattr(sender, "role", "") != "SENDER":
            return False
        if getattr(sender, "status", "") != "ACTIVE":
            return False
        from django.utils import timezone
        cooldown_until = getattr(sender, "cooldown_until", None)
        if cooldown_until and cooldown_until > timezone.now():
            return False
        return True

    @classmethod
    def get_tenant_pitch(cls, tenant_key: str, username: str, default_name: str = "") -> dict[str, Any]:
        """Loads tenant-isolated pitch configuration for an outreach account."""
        username_clean = username.lstrip("@").strip().lower()
        cfg = cls._load_config()

        # Check tenant-isolated pitches first
        tenant_pitches = cfg.get("tenant_pitches", {}).get(tenant_key, {})
        if username_clean in tenant_pitches:
            return tenant_pitches[username_clean]

        # Fallback to global sender_pitches for legacy single-tenant backward compatibility
        sender_pitches = cfg.get("sender_pitches", {})
        for k, v in sender_pitches.items():
            if k.lstrip("@").strip().lower() == username_clean:
                # If pitch belongs to legacy sarv and caller is tenant_4/sarv, allow fallback
                if tenant_key in ("tenant_4", "sarv"):
                    return v

        return {
            "consultant_name": default_name or username,
            "consultant_role": "مشاور و پشتیبانی",
            "template": "",
            "guardrails_validated": False,
        }

    @classmethod
    def save_tenant_pitch(cls, tenant_key: str, username: str, pitch_data: dict[str, Any]) -> None:
        """Saves pitch configuration isolated strictly to the given tenant using inter-process locks."""
        username_clean = username.lstrip("@").strip().lower()

        def _modifier(cfg: dict[str, Any]) -> None:
            if "tenant_pitches" not in cfg:
                cfg["tenant_pitches"] = {}
            if tenant_key not in cfg["tenant_pitches"]:
                cfg["tenant_pitches"][tenant_key] = {}

            cfg["tenant_pitches"][tenant_key][username_clean] = {
                "consultant_name": pitch_data.get("consultant_name", username),
                "consultant_role": pitch_data.get("consultant_role", "مشاور و پشتیبانی"),
                "template": pitch_data.get("template", ""),
                "guardrails_validated": bool(pitch_data.get("guardrails_validated", True)),
            }

            # Keep legacy sender_pitches in sync for tenant_4/sarv only
            if tenant_key in ("tenant_4", "sarv"):
                if "sender_pitches" not in cfg:
                    cfg["sender_pitches"] = {}
                cfg["sender_pitches"][username_clean] = cfg["tenant_pitches"][tenant_key][username_clean]

        cls._modify_and_save_config(_modifier)

    @classmethod
    def _modify_and_save_config(cls, modifier_fn: Any) -> None:
        """Executes read-modify-write cycle under an exclusive inter-process lock with atomic file replace."""
        import fcntl

        cfg_path = cls.CONFIG_PATH
        lock_path = cfg_path.with_suffix(".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with open(lock_path, "w", encoding="utf-8") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                cfg = cls._load_config()
                modifier_fn(cfg)
                temp_file = cfg_path.with_suffix(".tmp")
                temp_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                temp_file.replace(cfg_path)
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    @classmethod
    def _load_config(cls) -> dict[str, Any]:
        cfg_path = cls.CONFIG_PATH
        if not cfg_path.exists():
            return {}
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Could not load outreach config: %s", exc)
            return {}

    @classmethod
    def _save_config(cls, cfg: dict[str, Any]) -> None:
        try:
            cfg_path = cls.CONFIG_PATH
            cfg_path.parent.mkdir(parents=True, exist_ok=True)
            temp_file = cfg_path.with_suffix(".tmp")
            temp_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temp_file.replace(cfg_path)
        except Exception as exc:
            log.error("Could not save outreach config: %s", exc)
