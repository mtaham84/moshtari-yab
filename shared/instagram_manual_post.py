"""Shared request/result contract for operator-selected Instagram posts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ManualPostRequest:
    post_url: str
    business_id: int
    category_id: int | None = None
    product_id: int | None = None
    max_comments: int = 50
    daily_comment_cap: int = 100
    window_days: int = 7
    refresh: bool = False


@dataclass(frozen=True)
class ManualPostResult:
    status: str
    comments_fetched: int = 0
    comments_evaluated: int = 0
    human_review_comments: int = 0
    reason: str | None = None


class ManualPostRunner(Protocol):
    """Runtime boundary for future panel/task adapters; shared code imports no worker."""

    def run_manual_post(self, request: ManualPostRequest) -> ManualPostResult: ...
