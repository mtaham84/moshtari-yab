from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import NamedTuple


@dataclass
class ThrottleEntry:
    first_seen: float
    last_seen: float
    count: int
    sent_initial: bool


class ThrottleDecision(NamedTuple):
    should_send: bool
    count: int
    is_initial: bool


class ErrorThrottler:
    """Thread-safe rate limiter and deduplicator for log errors.

    Groups errors by fingerprint and suppresses duplicates within a cooldown window,
    tracking repeat counts so notifications are not spammed.
    """

    def __init__(self, cooldown_seconds: float = 900.0) -> None:
        self.cooldown_seconds = max(0.01, float(cooldown_seconds))
        self._lock = threading.Lock()
        self._records: dict[str, ThrottleEntry] = {}

    @staticmethod
    def compute_fingerprint(
        pathname: str,
        lineno: int,
        exc_type: str | None = None,
        msg: str | None = None,
    ) -> str:
        """Generates a stable identifier for an error location and type."""
        raw = f"{pathname}:{lineno}:{exc_type or ''}:{msg or ''}"
        return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]

    def evaluate(self, fingerprint: str) -> ThrottleDecision:
        """Determines whether a record should trigger an immediate Telegram alert.

        - If never seen, or if previous cooldown has elapsed: triggers alert (should_send=True).
        - If within cooldown: increments count and suppresses (should_send=False).
        """
        now = time.monotonic()
        with self._lock:
            entry = self._records.get(fingerprint)
            if entry is None or (now - entry.first_seen) >= self.cooldown_seconds:
                # New or expired window
                self._records[fingerprint] = ThrottleEntry(
                    first_seen=now,
                    last_seen=now,
                    count=1,
                    sent_initial=True,
                )
                self._cleanup_stale(now)
                return ThrottleDecision(should_send=True, count=1, is_initial=True)

            # Within cooldown window
            entry.count += 1
            entry.last_seen = now
            return ThrottleDecision(should_send=False, count=entry.count, is_initial=False)

    def _cleanup_stale(self, now: float) -> None:
        """Removes entries older than 2x cooldown to prevent memory growth."""
        if len(self._records) < 500:
            return
        cutoff = now - (self.cooldown_seconds * 2)
        stale_keys = [k for k, v in self._records.items() if v.last_seen < cutoff]
        for k in stale_keys:
            self._records.pop(k, None)

    def reset(self) -> None:
        """Clears all tracking history."""
        with self._lock:
            self._records.clear()
