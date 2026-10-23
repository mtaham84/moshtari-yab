from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable


@dataclass(frozen=True)
class DailyCap:
    limit: int
    used: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    def reached(self, additional: int = 0) -> bool:
        return self.used + additional >= self.limit


class Cooldown:
    def __init__(self, store, seconds: int = 900):
        self.store = store
        self.seconds = seconds

    def active_until(self, now: datetime | None = None) -> str | None:
        return self.store.cooldown_until(now)

    def start(self, now: datetime | None = None) -> str:
        return self.store.start_cooldown(self.seconds, now)


@dataclass
class CircuitBreaker:
    limit: int
    failures: int = 0

    def success(self) -> None:
        self.failures = 0

    def failure(self) -> bool:
        self.failures += 1
        return self.failures >= self.limit


def retry_delay(retry: int, jitter: Callable[[float, float], float] = random.uniform) -> float:
    noise = max(0.0, jitter(0.0, 0.5))
    return min(60.0, 2.0 ** retry) + noise
