"""
Tests for telemetry, heartbeat, and cycle finalizer seams (P08.12 / R39, R41).
"""

from __future__ import annotations

from pathlib import Path
import pytest

from workers.ig_collector.telemetry import (
    CollectorCycleFinalizer,
    safe_beat_error,
    safe_beat_ok,
)


def test_safe_heartbeat_survives_exceptions(monkeypatch):
    # Simulate a crash in underlying beat_ok
    def broken_beat_ok(platform, count):
        raise ConnectionResetError("Heartbeat daemon unreachable")

    def broken_beat_error(platform, reason, cooldown):
        raise TimeoutError("Heartbeat socket timed out")

    monkeypatch.setattr("workers.ig_collector.telemetry.beat_ok", broken_beat_ok)
    monkeypatch.setattr("workers.ig_collector.telemetry.beat_error", broken_beat_error)

    # Must not raise
    safe_beat_ok("instagram", 5)
    safe_beat_error("instagram", "test error", None)


def test_finalizer_manual_post_status():
    finalizer = CollectorCycleFinalizer(output_dir=Path("/tmp"), daily_cap=100, backend_name="mock")

    # Has review comments
    res_review = {"status": "selected", "human_review_comments": 2}
    finalizer.finalize_manual_post_status(res_review)
    assert res_review["status"] == "completed_with_review"

    # No review comments
    res_clean = {"status": "selected", "human_review_comments": 0}
    finalizer.finalize_manual_post_status(res_clean)
    assert res_clean["status"] == "completed"

    # Already finalized or failed status is untouched
    res_failed = {"status": "rate_limited"}
    finalizer.finalize_manual_post_status(res_failed)
    assert res_failed["status"] == "rate_limited"


def test_finalizer_build_result_payload():
    finalizer = CollectorCycleFinalizer(output_dir=Path("/tmp"), daily_cap=100, backend_name="mock")
    payload = finalizer.build_result_payload(
        status="COMPLETED",
        collected=[{"id": "1"}],
        filtered_out=3,
        failures=0,
        collected_today=15,
        output_file=Path("/tmp/out/2026-11-01.jsonl"),
        rejected_count=2,
        review_count=1,
        started_at="2026-11-01T20:00:00Z",
        handle_outcomes={"seller1": {"status": "ok", "posts": 1, "comments": 1}},
        cooldown_until=None,
        seller_lead_count=1,
        manual_post_result={"status": "completed"},
    )
    assert payload["status"] == "COMPLETED"
    assert payload["backend"] == "mock"
    assert payload["collected"] == 1
    assert payload["filtered_out"] == 3
    assert payload["seller_leads"] == 1
    assert payload["manual_post"]["status"] == "completed"
    assert "password" not in payload
    assert "token" not in payload
