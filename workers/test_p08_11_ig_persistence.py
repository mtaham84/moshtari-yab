"""
Tests for collector persistence manager seam (P08.11 / R39, R41).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import pytest

from workers.ig_collector.persistence import CollectorPersistenceManager


class MockStore:
    def __init__(self, fail_on_add: bool = False):
        self.rows = []
        self.fail_on_add = fail_on_add

    def add_many(self, items):
        if self.fail_on_add:
            raise RuntimeError("Database connection lost during store.add_many")
        self.rows.extend(items)


def test_persist_records_success(tmp_path):
    store = MockStore()
    manager = CollectorPersistenceManager(output_dir=tmp_path / "out", store=store)
    dated_file = tmp_path / "out" / "2026-11-01.jsonl"

    record = {
        "id": "ig:ABC123xyz",
        "source": "instagram",
        "kind": "post",
        "text": "سلام پست تستی",
        "author_handle": "@tester",
        "author_id": "author_1",
        "metadata": {"test": True},
    }
    persisted = manager.persist_records([record], dated_file)
    assert persisted == 1

    # Check files exist and content matches
    latest_file = tmp_path / "out" / "latest.jsonl"
    assert latest_file.exists()
    assert dated_file.exists()
    assert "ig:ABC123xyz" in latest_file.read_text(encoding="utf-8")
    assert "ig:ABC123xyz" in dated_file.read_text(encoding="utf-8")
    assert len(store.rows) == 1


def test_persist_records_atomic_rollback_on_failure(tmp_path):
    store = MockStore(fail_on_add=False)
    manager = CollectorPersistenceManager(output_dir=tmp_path / "out", store=store)
    dated_file = tmp_path / "out" / "2026-11-01.jsonl"
    latest_file = tmp_path / "out" / "latest.jsonl"

    # Pre-populate with existing valid record
    manager.persist_records(
        [{
            "id": "ig:Existing1",
            "source": "instagram",
            "kind": "post",
            "text": "پست قبلی",
            "author_handle": "@old",
            "author_id": "author_old",
        }],
        dated_file,
    )
    initial_latest_size = latest_file.stat().st_size
    initial_dated_size = dated_file.stat().st_size

    # Now attempt persisting with broken store
    store.fail_on_add = True
    with pytest.raises(RuntimeError, match="Database connection lost"):
        manager.persist_records(
            [{
                "id": "ig:BadRecord",
                "source": "instagram",
                "kind": "post",
                "text": "پست خراب",
                "author_handle": "@bad",
                "author_id": "author_bad",
            }],
            dated_file,
        )

    # Rollback must restore file sizes exactly
    assert latest_file.stat().st_size == initial_latest_size
    assert dated_file.stat().st_size == initial_dated_size
    assert "ig:BadRecord" not in latest_file.read_text(encoding="utf-8")


def test_persist_seller_leads_deduplication(tmp_path):
    store = MockStore()
    manager = CollectorPersistenceManager(output_dir=tmp_path / "out", store=store)

    leads = [
        {"id": "lead_1", "source": "instagram", "kind": "comment", "text": "خریدار کفش", "author_handle": "@u1", "author_id": "a1"},
        {"id": "lead_2", "source": "instagram", "kind": "comment", "text": "خریدار لباس", "author_handle": "@u2", "author_id": "a2"},
    ]
    # First persist
    c1 = manager.persist_seller_leads(leads)
    assert c1 == 2

    # Second persist with duplicates
    leads_with_dupe = [
        {"id": "lead_1", "source": "instagram", "kind": "comment", "text": "خریدار کفش", "author_handle": "@u1", "author_id": "a1"},
        {"id": "lead_3", "source": "instagram", "kind": "comment", "text": "خریدار مانتو", "author_handle": "@u3", "author_id": "a3"},
    ]
    c2 = manager.persist_seller_leads(leads_with_dupe)
    assert c2 == 1  # Only lead_3 was newly added

    target = tmp_path / "out" / "seller_leads.jsonl"
    lines = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    ids = [line["id"] for line in lines]
    assert ids == ["lead_1", "lead_2", "lead_3"]


def test_persist_review_queue_and_comment_evaluations(tmp_path):
    manager = CollectorPersistenceManager(output_dir=tmp_path / "out")

    # Review queue
    review_item = {
        "id": "ig:review_1",
        "source": "instagram",
        "kind": "post",
        "text": "بررسی نیاز دارد",
        "author_handle": "@uncertain_user",
        "author_id": None,
        "metadata": {"audit": False},
    }
    persisted_rq = manager.persist_review_queue([review_item])
    assert persisted_rq == 1

    # Re-inserting identical item does not duplicate
    persisted_rq_2 = manager.persist_review_queue([review_item])
    assert persisted_rq_2 == 0

    # Comment evaluations
    eval_row = {
        "id": "ig:Post1:c100",
        "business_id": "5",
        "comment_text": "قیمت چنده؟",
        "prefilter": {"status": "eligible"},
    }
    persisted_eval = manager.persist_comment_evaluations([eval_row])
    assert persisted_eval == 1

    # Re-inserting identical evaluation does not duplicate
    persisted_eval_2 = manager.persist_comment_evaluations([eval_row])
    assert persisted_eval_2 == 0


def test_write_status(tmp_path):
    manager = CollectorPersistenceManager(output_dir=tmp_path / "out")
    status_dict = {
        "status": "COMPLETED",
        "backend": "mock",
        "collected": 10,
        "output": "/tmp/out/2026-11-01.jsonl",
    }
    manager.write_status(status_dict, daily_cap=100)

    status_file = tmp_path / "out" / "status.json"
    assert status_file.exists()
    data = json.loads(status_file.read_text(encoding="utf-8"))
    assert data["status"] == "COMPLETED"
    assert data["daily_cap"] == 100
    assert data["output"] == "2026-11-01.jsonl"
