from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

from workers.ig_collector.backends.base import InstagramPost
from workers.ig_collector.post_selection import select_comment_candidates


NOW = datetime.now(timezone.utc)


def candidate(shortcode, result="human_review", business_id=7):
    post = InstagramPost(
        post_id=shortcode, shortcode=shortcode, caption="کفش چرمی برای سفارش",
        username="shop", created_at=NOW,
    )
    decision = {
        "result": result, "source_category_id": 2,
        "category_id": 2 if result == "confident_match" else None,
        "category_candidate_id": 2, "category_candidate": "کفش",
        "score": 0.86 if result == "confident_match" else 0.55,
        "reason": {"code": "matched" if result == "confident_match" else "uncertain_score",
                   "evidence": {"caption_terms": ["کفش"]}},
    }
    record = {
        "id": f"ig:{shortcode}",
        "metadata": {
            "collection_source": {"kind": "hashtag", "business_id": business_id, "category_id": 2},
            "suggested_target": {"source": "classifier", "business_id": business_id, "category_id": 2},
            "category_decision": decision,
            "parent_post": {"id": f"ig:{shortcode}", "category_decision": deepcopy(decision)},
        },
    }
    return post, "hashtag:کفش", record


def test_operator_approval_admits_uncertain_post_and_preserves_classifier_output():
    row = candidate("Abcde12")
    original_decision = deepcopy(row[2]["metadata"]["category_decision"])
    selected = select_comment_candidates(
        [row], since=NOW - timedelta(days=1),
        post_labels={("7", "ig:Abcde12"): {
            "decision": "approve", "category_id": "9", "product_id": "12", "audit": False,
        }},
    )

    assert len(selected) == 1
    selected_record = selected[0][2]
    assert selected_record["metadata"]["category_decision"] == original_decision
    assert selected_record["metadata"]["suggested_target"] == {
        "source": "operator", "business_id": "7", "category_id": "9", "product_id": "12", "score": None,
    }
    assert selected_record["metadata"]["parent_post"]["suggested_target"]["source"] == "operator"
    assert row[2]["metadata"]["suggested_target"]["source"] == "classifier"


def test_rejected_confident_and_pending_uncertain_posts_do_not_enter_gate():
    confident = candidate("Conf12", result="confident_match")
    uncertain = candidate("Wait12")
    selected = select_comment_candidates(
        [confident, uncertain], since=NOW - timedelta(days=1),
        post_labels={("7", "ig:Conf12"): {"decision": "reject", "category_id": None}},
    )

    assert selected == []


def test_labels_from_another_business_are_ignored_and_confident_posts_keep_existing_gate():
    uncertain = candidate("Other12", business_id=7)
    confident = candidate("Sure12", result="confident_match", business_id=7)
    selected = select_comment_candidates(
        [uncertain, confident], since=NOW - timedelta(days=1),
        post_labels={("8", "ig:Other12"): {
            "decision": "approve", "category_id": "99", "product_id": None,
        }},
    )

    assert [row[0].shortcode for row in selected] == ["Sure12"]
    assert selected[0][2]["metadata"]["category_decision"]["result"] == "confident_match"
    assert "operator_post_label" not in selected[0][2]["metadata"]


def test_confident_match_still_requires_exact_category_and_recent_post():
    confident = candidate("Sure12", result="confident_match")
    confident[2]["metadata"]["category_decision"]["category_id"] = 3
    old_post = candidate("Old123", result="confident_match")
    old_post = (InstagramPost(
        post_id="Old123", shortcode="Old123", caption="old",
        username="shop", created_at=NOW - timedelta(days=2),
    ), old_post[1], old_post[2])

    assert select_comment_candidates(
        [confident, old_post], since=NOW - timedelta(days=1),
    ) == []
