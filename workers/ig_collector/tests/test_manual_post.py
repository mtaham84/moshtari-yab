from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from instagram_discovery.comment_evaluation import KnowledgeSnapshot
from shared.instagram_manual_post import ManualPostRequest
from workers.ig_collector.backends.base import (
    AuthFailed, InstagramComment, NotFound, PrivateProfile, RateLimited, Transient,
)
from workers.ig_collector.backends.mock import MockInstagramBackend
from workers.ig_collector.cli import _main
from workers.ig_collector.hikerapi_mapping import shortcode_to_media_id
from workers.ig_collector.worker import InstagramCollector


CODE = "AbCde12"
URL = f"https://www.instagram.com/p/{CODE}/?source=operator#comments"


@pytest.fixture(autouse=True)
def collector_tests_do_not_write_database_heartbeats(monkeypatch):
    monkeypatch.setattr("workers.ig_collector.worker.beat_ok", lambda *args: None)
    monkeypatch.setattr("workers.ig_collector.worker.beat_error", lambda *args: None)


def fixture_data(comment_count: int = 4) -> dict:
    return {
        "posts": {CODE: {
            "post_id": "fixture-post-1", "shortcode": CODE,
            "caption": "محصول جدید با رنگ‌بندی متنوع برای سفارش موجود است",
            "username": "seller_fixture",
            "created_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        }},
        "comments": {CODE: [
            {"comment_id": f"comment-{index}",
             "text": "برای خرید این مدل قیمت و رنگ موجود را می‌خواستم بدانم",
             "username": f"buyer_{index}", "author_id": str(100 + index),
             "created_at": datetime.now(timezone.utc).isoformat()}
            for index in range(comment_count)
        ]},
    }


class Knowledge:
    def __init__(self):
        self.calls = []

    def retrieve_for_comment(self, *, comment_text, category_id, product_id, post_context):
        self.calls.append((category_id, product_id, dict(post_context)))
        return KnowledgeSnapshot(
            related_category={"id": category_id, "name": "Shoes"} if category_id else None,
            related_product={"id": product_id, "name": "Boots"} if product_id else None,
            answers=["Available colors and sizes"], indexed_information=["catalog passage"],
            source={"kind": "mock_index", "document_id": "catalog-1"},
        )


class Jev:
    def __init__(self, status="uncertain"):
        self.calls = []
        self.status = status

    def evaluate_comment(self, *, comment_text, knowledge, post_context):
        self.calls.append((comment_text, knowledge, dict(post_context)))
        return {"status": self.status, "related": {"category": {"id": "12"}, "product": None},
                "score": 0.4, "reason": "The question may indicate purchase interest.",
                "evidence": ["برای خرید"]}


class CountingMock(MockInstagramBackend):
    def __init__(self, fixture):
        super().__init__(fixture)
        self.comment_limits = []

    def post_comments(self, post_ref, limit):
        self.comment_limits.append(limit)
        return super().post_comments(post_ref, limit)


def collector(tmp_path: Path, backend=None, **kwargs):
    return InstagramCollector(
        backend or MockInstagramBackend(fixture_data()), output_dir=tmp_path / "out",
        state_path=tmp_path / "state.sqlite3", daily_cap=100, sleep_min=0,
        sleep_max=0, sleeper=lambda _: None, jitter=lambda low, high: 0,
        max_retries=0, **kwargs,
    )


def request(**changes):
    values = {"post_url": URL, "business_id": 4, "category_id": 12,
              "max_comments": 50, "window_days": 7}
    values.update(changes)
    return ManualPostRequest(**values)


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_shortcode_to_media_id_is_local_and_validated():
    assert shortcode_to_media_id("AAAAA") == "0"
    assert shortcode_to_media_id("AAAAB") == "1"
    with pytest.raises(ValueError):
        shortcode_to_media_id("bad!")


def test_manual_post_mock_end_to_end_uses_protocol_jev_and_review(tmp_path):
    knowledge, jev = Knowledge(), Jev()
    service = collector(tmp_path, knowledge_reader=knowledge, jev=jev)

    result = service.run_manual_post(request())

    assert result.status == "completed_with_review"
    assert result.comments_fetched == 4
    assert result.comments_evaluated == 4
    assert result.human_review_comments == 4
    assert len(knowledge.calls) == len(jev.calls) == 4
    category_id, product_id, context = knowledge.calls[0]
    assert category_id == "12" and product_id is None
    assert context["suggested_target"] == {
        "source": "operator", "business_id": 4, "category_id": 12, "product_id": None,
    }
    assert "score" not in context["suggested_target"]
    evaluations = read_jsonl(tmp_path / "out" / "comment-evaluations.jsonl")
    assert evaluations[0]["collection_source"]["kind"] == "manual_post"
    assert evaluations[0]["jev"]["status"] == "uncertain"
    assert evaluations[0]["jev"]["indexed_information_used"]["source"]["document_id"] == "catalog-1"
    queued = read_jsonl(tmp_path / "out" / "needs-review.jsonl")
    assert len(queued) == 4


def test_manual_post_comment_cap_is_passed_to_backend_and_respected(tmp_path):
    backend = CountingMock(fixture_data(10))
    result = collector(tmp_path, backend=backend).run_manual_post(request(max_comments=2))
    assert result.comments_fetched == 2
    assert backend.comment_limits == [2]
    assert len(read_jsonl(tmp_path / "out" / "comment-evaluations.jsonl")) == 2


def test_manual_post_daily_comment_cap_persists_across_refresh(tmp_path):
    backend = CountingMock(fixture_data(10))
    service = collector(tmp_path, backend=backend)
    first = service.run_manual_post(request(daily_comment_cap=2))
    second = service.run_manual_post(request(daily_comment_cap=2, refresh=True))

    assert first.comments_fetched == 2
    assert second.status == "daily_comment_cap_reached"
    assert second.comments_fetched == 0
    assert backend.comment_limits == [2]


def test_same_post_is_idempotent_and_refresh_deduplicates_comment_ids(tmp_path):
    backend = CountingMock(fixture_data(2))
    service = collector(tmp_path, backend=backend)
    first = service.run_manual_post(request(max_comments=2))
    second = service.run_manual_post(request(max_comments=2))
    refreshed = service.run_manual_post(request(max_comments=2, refresh=True))

    assert first.comments_fetched == 2
    assert second.status == "already_fetched"
    assert second.comments_fetched == 0
    assert refreshed.comments_fetched == 2
    assert backend.comment_limits == [2, 2]
    assert len(read_jsonl(tmp_path / "out" / "comment-evaluations.jsonl")) == 2


@pytest.mark.parametrize("error, expected", [
    (NotFound("private shortcode identity must not leak"), "not_found"),
    (PrivateProfile("private identity must not leak"), "private"),
    (AuthFailed("credential detail must not leak"), "auth_failed"),
    (RateLimited("provider detail must not leak"), "rate_limited"),
    (Transient("network identity must not leak"), "transient"),
])
def test_manual_backend_errors_are_safe_structured_outcomes(tmp_path, error, expected):
    class ErrorBackend(MockInstagramBackend):
        def post_by_shortcode(self, shortcode):
            raise error

    service = collector(tmp_path, backend=ErrorBackend(fixture_data()))
    result = service.run_manual_post(request())
    assert result.status == expected
    status_text = (tmp_path / "out" / "status.json").read_text(encoding="utf-8")
    assert URL not in status_text
    assert CODE not in status_text
    assert str(error) not in status_text


def test_invalid_manual_link_is_structured_and_does_not_contact_backend(tmp_path):
    class NoLookupBackend(MockInstagramBackend):
        def post_by_shortcode(self, shortcode):
            raise AssertionError("invalid links must be rejected before backend access")

    result = collector(tmp_path, backend=NoLookupBackend(fixture_data())).run_manual_post(
        request(post_url="https://evil.example/p/AbCde12")
    )
    assert result.status == "invalid_link"
    assert result.reason == "invalid_host"


def test_cli_accepts_manual_post_arguments_and_runs_core(tmp_path, capsys):
    exit_code = _main([
        "--mock", "--once", "--post", "https://instagram.com/p/IGAd102/",
        "--business-id", "1", "--category-id", "2", "--max-comments", "1",
        "--window-days", "30",
    ], project_root=tmp_path)
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["status"] == "completed_with_review"
    assert output["comments_fetched"] == 1
    assert output["human_review_comments"] == 1


def test_manual_post_does_not_introduce_need_engine_or_apps_worker_edge():
    root = Path(__file__).resolve().parents[3]
    worker_source = (root / "workers" / "ig_collector" / "worker.py").read_text(encoding="utf-8")
    assert "need_engine" not in worker_source
    for source_path in (root / "apps").rglob("*.py"):
        if any(part in {"tests", "migrations"} for part in source_path.parts):
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            assert not any(name.startswith("workers.ig_collector") for name in names), source_path
