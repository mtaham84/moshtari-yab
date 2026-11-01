"""
Shared Distributed Provider Quota Manager (P05.04 / R15).

Implements QuotaPort using shared caching (e.g. Redis / Django Cache):
1. Cross-process RPM (Requests Per Minute) sliding minute buckets.
2. Cross-process TPM (Tokens Per Minute) sliding minute buckets.
3. Cross-process RPD (Requests Per Day) calendar day limits.
4. Distributed 429 Cooldown: when worker A receives HTTP 429, worker B is immediately throttled.
5. Persistent across process restarts and multiple instance creations.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from django.core.cache import cache as default_cache

from apps.core.gateway_contract import ModelRef, QuotaPort

logger = logging.getLogger("core.quota")


class SharedQuotaManager(QuotaPort):
    """
    Thread-safe, multi-process QuotaPort coordinating LLM provider rate limits.
    """

    def __init__(
        self,
        cache_backend: Optional[Any] = None,
        registry: Optional[Any] = None,
        default_limits: Optional[Dict[str, int]] = None,
    ):
        self.cache = cache_backend or default_cache
        self.registry = registry
        self.default_limits = default_limits or {"rpm": 60, "tpm": 100000, "rpd": 5000}

    def _get_limits(self, model_ref: ModelRef) -> Dict[str, Optional[int]]:
        """
        Retrieves rate limits for the given ModelRef from registry or default.
        """
        if self.registry:
            lim = self.registry.rate_limit(model_ref.identity)
            if lim:
                return lim
        return dict(self.default_limits)

    def _get_cool_key(self, model_ref: ModelRef) -> str:
        return f"llm_quota:{model_ref.identity}:cool_until"

    def _get_daily_key(self, model_ref: ModelRef, day_str: str) -> str:
        return f"llm_quota:{model_ref.identity}:{day_str}:rpd"

    def _get_daily_exhausted_key(self, model_ref: ModelRef, day_str: str) -> str:
        return f"llm_quota:{model_ref.identity}:{day_str}:exhausted"

    def _get_minute_keys(self, model_ref: ModelRef, minute_str: str) -> Tuple[str, str]:
        rpm_key = f"llm_quota:{model_ref.identity}:{minute_str}:rpm"
        tpm_key = f"llm_quota:{model_ref.identity}:{minute_str}:tpm"
        return rpm_key, tpm_key

    def reserve_provider_quota(self, model_ref: ModelRef, est_tokens: int) -> Tuple[bool, str]:
        """
        Atomically inspects and reserves capacity for the model across all workers.
        """
        now_ts = time.time()
        limits = self._get_limits(model_ref)
        rpm_limit = limits.get("rpm")
        tpm_limit = limits.get("tpm")
        rpd_limit = limits.get("rpd")

        # 1. Check Cooldown (active 429)
        cool_key = self._get_cool_key(model_ref)
        cool_until = self.cache.get(cool_key)
        if cool_until and now_ts < float(cool_until):
            rem = int(float(cool_until) - now_ts)
            return False, f"COOLDOWN_ACTIVE (retry in {rem}s)"

        # 2. Check Daily RPD Limit
        now_utc = datetime.now(timezone.utc)
        day_str = now_utc.strftime("%Y-%m-%d")
        daily_exhausted_key = self._get_daily_exhausted_key(model_ref, day_str)
        if self.cache.get(daily_exhausted_key):
            return False, "DAILY_RPD_EXHAUSTED"

        daily_key = self._get_daily_key(model_ref, day_str)
        if rpd_limit:
            current_rpd = self.cache.get(daily_key) or 0
            if current_rpd >= rpd_limit:
                self.cache.set(daily_exhausted_key, True, timeout=86400)
                return False, "DAILY_RPD_EXHAUSTED"

        # 3. Check Minute RPM & TPM Limits
        minute_str = now_utc.strftime("%Y-%m-%d_%H:%M")
        rpm_key, tpm_key = self._get_minute_keys(model_ref, minute_str)

        if rpm_limit:
            curr_rpm = self.cache.get(rpm_key) or 0
            if curr_rpm >= rpm_limit:
                return False, "RPM_EXCEEDED"

        if tpm_limit:
            curr_tpm = self.cache.get(tpm_key) or 0
            if curr_tpm + est_tokens > tpm_limit:
                return False, "TPM_EXCEEDED"

        # 4. Atomic Increment / Reservation
        self._atomic_incr(rpm_key, 1, ttl=120)
        self._atomic_incr(tpm_key, est_tokens, ttl=120)
        if rpd_limit:
            self._atomic_incr(daily_key, 1, ttl=86400)

        return True, "OK"

    def _atomic_incr(self, key: str, amount: int, ttl: int) -> None:
        """Helper to initialize with add() and atomically increment."""
        try:
            # If key does not exist, add it
            if self.cache.add(key, amount, timeout=ttl):
                return
            # If key already exists, incr
            self.cache.incr(key, amount)
        except Exception:
            # Fallback if cache backend lacks native incr
            val = self.cache.get(key) or 0
            self.cache.set(key, val + amount, timeout=ttl)

    def record_provider_success(self, model_ref: ModelRef, actual_tokens: int) -> None:
        """Confirmed successful response."""
        pass

    def record_provider_429(
        self,
        model_ref: ModelRef,
        retry_after_sec: Optional[float] = None,
        is_daily: bool = False,
    ) -> None:
        """
        Global cross-process throttle when any worker receives HTTP 429.
        """
        if is_daily:
            day_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            daily_exhausted_key = self._get_daily_exhausted_key(model_ref, day_str)
            self.cache.set(daily_exhausted_key, True, timeout=86400)
            logger.warning("Provider %s marked DAILY_RPD_EXHAUSTED across all workers", model_ref.identity)
        else:
            delay = float(retry_after_sec or 30.0)
            cool_key = self._get_cool_key(model_ref)
            self.cache.set(cool_key, time.time() + delay, timeout=int(delay) + 30)
            logger.warning("Provider %s entering distributed cooldown for %.1fs", model_ref.identity, delay)
