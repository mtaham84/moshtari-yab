"""
Pure planning, seed segregation, and comments configuration builder for Instagram Collector (P08.08 / R39).

Extracts validation and run-plan decisions out of the monolithic _run_locked orchestrator.
Completely testable offline with fixtures, zero database or network dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CollectionPlan:
    manual_seeds: List[Dict[str, Any]] = field(default_factory=list)
    account_seeds: List[Dict[str, Any]] = field(default_factory=list)
    hashtag_seeds: List[Dict[str, Any]] = field(default_factory=list)
    effective_comments: Dict[str, Any] = field(default_factory=dict)
    policy_name: str = "buyer_request"
    is_rate_limited: bool = False
    cooldown_until: Optional[str] = None
    daily_cap: int = 100
    available_capacity: int = 100


def adjust_manual_comments_config(
    comments: Dict[str, Any],
    manual_seeds: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Adjusts comments collection limits when manual post seeds are supplied.
    Ensures mode is 'all' and max_per_post and window_days encompass the manual seeds' requirements.
    """
    if not manual_seeds:
        return dict(comments)

    def _as_int(v: Any, default: int = 0) -> int:
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    max_manual_comments = max([_as_int(s.get("max_comments", 0)) for s in manual_seeds] or [0])
    max_manual_window = max([_as_int(s.get("window_days", 1)) for s in manual_seeds] or [1])

    adjusted = dict(comments)
    adjusted["mode"] = "all"
    adjusted["max_posts_per_run"] = min(100, max(1, _as_int(comments.get("max_posts_per_run", 0))))
    adjusted["max_per_post"] = min(500, max(_as_int(comments.get("max_per_post", 0)), max_manual_comments))
    adjusted["window_days"] = max(_as_int(comments.get("window_days", 1)), max_manual_window)

    return adjusted


def build_collection_plan(
    seeds: List[Dict[str, Any]],
    comments: Optional[Dict[str, Any]] = None,
    *,
    daily_cap: int = 100,
    collected_today: int = 0,
    cooldown_until: Optional[str] = None,
    supported_policies: Optional[set[str]] = None,
) -> CollectionPlan:
    """
    Constructs a deterministic CollectionPlan from inputs.
    Separates seeds by kind, adjusts comment policies, and evaluates initial rate-limit / capacity states.
    """
    comments_cfg = dict(comments or {})
    valid_policies = supported_policies or {"buyer_request", "seller_post", "deep_leads"}

    manual_seeds: List[Dict[str, Any]] = []
    account_seeds: List[Dict[str, Any]] = []
    hashtag_seeds: List[Dict[str, Any]] = []

    for seed in seeds:
        kind = seed.get("kind")
        if kind == "post":
            manual_seeds.append(seed)
        elif kind == "account":
            account_seeds.append(seed)
        elif kind == "hashtag":
            hashtag_seeds.append(seed)

    effective_comments = adjust_manual_comments_config(comments_cfg, manual_seeds)
    policy_name = effective_comments.get("policy") or "buyer_request"
    if policy_name not in valid_policies:
        policy_name = "buyer_request"

    effective_comments["policy"] = policy_name

    available_capacity = max(0, daily_cap - collected_today)
    is_rate_limited = bool(cooldown_until)

    return CollectionPlan(
        manual_seeds=manual_seeds,
        account_seeds=account_seeds,
        hashtag_seeds=hashtag_seeds,
        effective_comments=effective_comments,
        policy_name=policy_name,
        is_rate_limited=is_rate_limited,
        cooldown_until=cooldown_until,
        daily_cap=daily_cap,
        available_capacity=available_capacity,
    )
