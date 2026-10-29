"""Negative/positive scoring of Instagram search terms from the crawl history.

Instagram discovery stays focused on a business's core keywords and hashtags. Each time a keyword or hashtag is
searched, ``InstagramCrawlExecution`` records how many posts qualified and how many leads came out. A term that is
searched repeatedly without finding any lead collects negative score; a term that produced leads collects positive
score. Selection then samples from Beta(1 + leads, 1 + penalty) (Thompson Sampling) over a relevance-first shortlist,
so weak terms fade out without ever being banned for good (they keep a small chance and are re-tried as history ages).
"""
from __future__ import annotations

from datetime import timedelta
import random
from typing import Any, Callable

HISTORY_DAYS = 30
EMPTY_RUN_PENALTY = 0.5     # a run with no lead
FAILED_RUN_PENALTY = 0.25   # a failed/throttled run says little about the term itself


def history_for(labels: list[str], target_type: str) -> dict[str, dict[str, float]]:
    """{label: {"runs", "leads", "penalty"}} over the last HISTORY_DAYS days."""
    from django.utils import timezone

    from apps.discovery.models import InstagramCrawlExecution

    since = timezone.now() - timedelta(days=HISTORY_DAYS)
    rows = InstagramCrawlExecution.objects.filter(
        target_type=target_type, query_text__in=labels, crawled_at__gte=since,
    ).values_list("query_text", "status", "leads_found")
    out: dict[str, dict[str, float]] = {}
    for label, status, leads in rows:
        h = out.setdefault(label, {"runs": 0.0, "leads": 0.0, "penalty": 0.0})
        h["runs"] += 1
        h["leads"] += leads or 0
        if (leads or 0) == 0:
            h["penalty"] += FAILED_RUN_PENALTY if status in {"FAILED", "THROTTLED"} else EMPTY_RUN_PENALTY
    return out


def score(history: dict[str, float] | None, rng: random.Random | None = None) -> float:
    """One Thompson sample; unseen terms get Beta(1, 1) so they are still explored."""
    rng = rng or random
    h = history or {"leads": 0.0, "penalty": 0.0}
    return rng.betavariate(1.0 + h["leads"], 1.0 + h["penalty"])


def pick(shortlist: list[str], k: int, histories: dict[str, dict[str, float]],
         label_of: Callable[[str], str] = lambda x: x, rng: random.Random | None = None) -> list[str]:
    """Best ``k`` of ``shortlist`` by sampled score."""
    ranked = sorted(shortlist, key=lambda item: -score(histories.get(label_of(item)), rng))
    return ranked[:k]


def select_scored(candidates: list[str], k: int, target_type: str, mmr_select: Callable[[list[str], int], list[str]],
                  label_of: Callable[[str], str] = lambda x: x, shortlist_factor: int = 2,
                  rng: random.Random | None = None) -> list[str]:
    """Relevance/diversity shortlist (MMR, ``shortlist_factor`` x k) -> top k by history score."""
    if len(candidates) <= k:
        return list(candidates)
    shortlist = mmr_select(candidates, min(len(candidates), k * shortlist_factor))
    try:
        histories = history_for([label_of(c) for c in shortlist], target_type)
    except Exception:  # history is an optimisation: never block crawling on it
        return shortlist[:k]
    return pick(shortlist, k, histories, label_of, rng)
