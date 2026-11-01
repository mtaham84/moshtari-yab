"""
Telemetry, heartbeat, and cycle finalization seam for Instagram collector (P08.12 / R39, R41).

Decouples telemetry and reporting from core business decisions.
Ensures best-effort telemetry never crashes the worker or hides domain failures.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from shared.collector_kit.heartbeat import beat_error, beat_ok
from instagram_discovery.post_metrics import sample_confident_audit_posts

log = logging.getLogger("ig_collector.telemetry")


def safe_beat_ok(platform: str = "instagram", collected: int = 0) -> None:
    """
    Best-effort heartbeat notification. Errors are logged but never raised.
    """
    try:
        beat_ok(platform, collected)
    except Exception as exc:
        log.warning("Heartbeat beat_ok failed (non-fatal): %s", exc)


def safe_beat_error(platform: str = "instagram", reason: str = "unknown", cooldown_until: Optional[str] = None) -> None:
    """
    Best-effort error heartbeat notification. Errors are logged but never raised.
    """
    try:
        beat_error(platform, reason, cooldown_until)
    except Exception as exc:
        log.warning("Heartbeat beat_error failed (non-fatal): %s", exc)


class CollectorCycleFinalizer:
    """
    Coordinates post-crawl audit sampling, persistence dispatch, status resolution,
    and final telemetry payload generation.
    """

    def __init__(self, output_dir: Path, daily_cap: int, backend_name: str):
        self.output_dir = output_dir
        self.daily_cap = daily_cap
        self.backend_name = backend_name

    def sample_audit_candidates(
        self,
        audit_candidates: List[Dict[str, Any]],
        comments_config: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Samples confident audit posts according to configured sample rate and limits.
        """
        if not audit_candidates:
            return []
        try:
            audit_rate = float(comments_config.get("post_audit_sample_rate", 0.05))
            audit_limit = max(0, min(100, int(comments_config.get("post_audit_max_items", 25))))
            audit_seed = int(comments_config.get("post_audit_seed", 0))
            return sample_confident_audit_posts(
                audit_candidates,
                sample_rate=audit_rate,
                max_items=audit_limit,
                seed=audit_seed,
            )
        except Exception as exc:
            log.warning("Audit candidate sampling failed; skipping extra audits: %s", exc)
            return []

    def finalize_manual_post_status(self, manual_post_result: Optional[Dict[str, Any]]) -> None:
        """
        Finalizes manual post status string based on whether human review comments exist.
        """
        if manual_post_result is not None and manual_post_result.get("status") == "selected":
            if manual_post_result.get("human_review_comments"):
                manual_post_result["status"] = "completed_with_review"
            else:
                manual_post_result["status"] = "completed"

    def build_result_payload(
        self,
        status: str,
        collected: List[Dict[str, Any]],
        filtered_out: int,
        failures: int,
        collected_today: int,
        output_file: Path,
        rejected_count: int,
        review_count: int,
        started_at: str,
        handle_outcomes: Dict[str, Any],
        cooldown_until: Optional[str] = None,
        seller_lead_count: int = 0,
        manual_post_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Builds the canonical cycle result dictionary without leaking secrets or credentials.
        """
        result = {
            "status": status,
            "backend": self.backend_name,
            "collected": len(collected),
            "filtered_out": filtered_out,
            "failures": failures,
            "collected_today": collected_today,
            "output": str(output_file),
            "rejected": rejected_count,
            "human_review": review_count,
            "started_at": started_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "handles": handle_outcomes,
            "cooldown_until": cooldown_until,
            "seller_leads": seller_lead_count,
        }
        if manual_post_result is not None:
            result["manual_post"] = dict(manual_post_result)
        return result
