"""
Tests for comment evaluation and filtering pipeline (P08.10 / R39).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import pytest

from workers.ig_collector.backends.base import InstagramComment, InstagramPost
from workers.ig_collector.evaluator import (
    CommentEvaluationOutcome,
    CommentEvaluationPipeline,
)
from workers.ig_collector.lexicon import PersianIntentLexicon
from workers.ig_collector.worker import normalize_comment


@pytest.fixture
def lexicon():
    path = Path(__file__).resolve().parents[1] / "data" / "ig_lexicon_fa.json"
    return PersianIntentLexicon.load(path)


@pytest.fixture
def pipeline(lexicon):
    return CommentEvaluationPipeline(
        lexicon=lexicon,
        knowledge_reader=None,
        jev=None,
        min_intent_score=1,
        policy_name="seller_post",
    )


@pytest.fixture
def sample_post():
    return InstagramPost(
        post_id="post_999",
        shortcode="POST999",
        caption="فروش مانتو پاییزه ارسال به سراسر کشور",
        username="manto_shop",
        created_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def sample_post_record(sample_post):
    return {
        "id": f"ig:{sample_post.shortcode}",
        "kind": "post",
        "text": sample_post.caption,
        "author_handle": f"@{sample_post.username}",
        "metadata": {
            "suggested_target": {"business_id": 42, "category_id": 10},
        },
    }


def test_evaluator_rejects_non_persian_comment(pipeline, sample_post, sample_post_record):
    comment = InstagramComment(
        comment_id="c_eng_1",
        username="foreign_user",
        text="Hello how much is this jacket?",
        created_at=datetime.now(timezone.utc),
    )
    outcome = pipeline.evaluate_comment(
        comment=comment,
        post=sample_post,
        post_record=sample_post_record,
        parent_post={"id": f"ig:{sample_post.shortcode}"},
        collection_source={"kind": "account", "username": sample_post.username},
        seed_key="account:manto_shop",
        backend_name="test_backend",
        normalize_fn=normalize_comment,
    )
    assert outcome.accepted is False
    assert outcome.is_filtered is True
    assert outcome.record is None
    assert outcome.rejection_reason == "invalid_or_non_persian_or_missing_fields"
    assert outcome.evaluation_row["prefilter"]["status"] == "not_evaluable"
    assert outcome.evaluation_row["business_id"] == "42"


def test_evaluator_prefilters_self_comment(pipeline, sample_post, sample_post_record):
    # Post author commenting on their own post
    comment = InstagramComment(
        comment_id="c_self_1",
        username="manto_shop",
        text="دایرکت چک بفرمایید عزیزم",
        created_at=datetime.now(timezone.utc),
    )
    outcome = pipeline.evaluate_comment(
        comment=comment,
        post=sample_post,
        post_record=sample_post_record,
        parent_post={"id": f"ig:{sample_post.shortcode}"},
        collection_source={"kind": "account", "username": sample_post.username},
        seed_key="account:manto_shop",
        backend_name="test_backend",
        normalize_fn=normalize_comment,
    )
    assert outcome.accepted is False
    assert outcome.is_filtered is True
    assert outcome.rejection_reason == "set_aside"
    assert outcome.evaluation_row["prefilter"]["status"] == "set_aside"
    assert outcome.keep_rejected_reason is None  # set_aside should not clutter keep_rejected


def test_evaluator_accepts_valid_buyer_lead(pipeline, sample_post, sample_post_record):
    comment = InstagramComment(
        comment_id="c_buyer_1",
        username="real_buyer",
        text="سلام وقت بخیر قیمت سایز ۴۰ چنده؟ موجود دارید بخرم؟",
        created_at=datetime.now(timezone.utc),
    )
    outcome = pipeline.evaluate_comment(
        comment=comment,
        post=sample_post,
        post_record=sample_post_record,
        parent_post={"id": f"ig:{sample_post.shortcode}"},
        collection_source={"kind": "account", "username": sample_post.username},
        seed_key="account:manto_shop",
        backend_name="test_backend",
        normalize_fn=normalize_comment,
    )
    assert outcome.accepted is True
    assert outcome.is_filtered is False
    assert outcome.record is not None
    assert outcome.record["author_handle"] == "@real_buyer"
    assert "jev_evaluation" in outcome.record["metadata"]


def test_evaluator_handles_uncertain_jev_routing(pipeline, sample_post, sample_post_record):
    # When jev is None, evaluate_with_jev returns uncertain status
    comment = InstagramComment(
        comment_id="c_buyer_2",
        username="curious_person",
        text="قیمت چنده لطفا؟",
        created_at=datetime.now(timezone.utc),
    )
    outcome = pipeline.evaluate_comment(
        comment=comment,
        post=sample_post,
        post_record=sample_post_record,
        parent_post={"id": f"ig:{sample_post.shortcode}"},
        collection_source={"kind": "account", "username": sample_post.username},
        seed_key="account:manto_shop",
        backend_name="test_backend",
        normalize_fn=normalize_comment,
    )
    # Uncertain status must be flagged for human review queue
    assert outcome.is_uncertain is True
    assert outcome.record["metadata"]["review_queue_status"] == "queued"
    assert outcome.record["metadata"]["human_review_required"] is True


def test_evaluator_creates_capped_row(sample_post, sample_post_record):
    comment = InstagramComment(
        comment_id="c_capped_1",
        username="buyer_late",
        text="تست نظر بعد از پر شدن سقف",
        created_at=datetime.now(timezone.utc),
    )
    row = CommentEvaluationPipeline.create_capped_evaluation_row(
        comment=comment,
        post=sample_post,
        post_record=sample_post_record,
        collection_source={"kind": "account", "username": "manto_shop"},
        comment_index=5,
    )
    assert row["id"] == "ig:POST999:cc_capped_1"
    assert row["prefilter"]["status"] == "not_evaluable"
    assert row["prefilter"]["reason"] == "daily_cap_reached"
    assert row["business_id"] == "42"
