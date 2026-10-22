from __future__ import annotations

import json

import pytest

from instagram_discovery.post_metrics import (
    build_post_report,
    load_post_labels,
    pending_post_reviews,
    purge_post_review_data,
    sample_confident_audit_posts,
    save_post_label,
)


def post(post_id, business_id=7, result="human_review", score=0.55, reason="uncertain_score",
         category_id=2, audit=False):
    return {
        "id": f"ig:{post_id}", "source": "instagram", "kind": "post",
        "text": "کپشن فارسی <script>alert(1)</script>", "created_at": "2026-10-23T10:00:00+00:00",
        "url": f"https://www.instagram.com/p/{post_id}/",
        "metadata": {
            "hashtags": ["کفش"], "collection_source": {"kind": "hashtag", "business_id": business_id},
            "suggested_target": {"business_id": business_id, "category_id": 2},
            "audit": audit,
            "category_decision": {
                "result": result, "score": score, "category_candidate_id": category_id,
                "category_candidate": "کفش", "product_candidate_id": 3, "product_candidate": "کفش چرمی",
                "reason": {"code": reason, "evidence": {"caption_excerpt": "کفش <script>", "post_hashtags": ["کفش"]}},
                "knowledge_source": {"kind": "seller_category_seed", "category_id": category_id},
            },
        },
    }


def snapshot(result="human_review", score=0.55, reason="uncertain_score", category_id=2, product_id=3):
    return {
        "result": result, "score": score, "reason_code": reason,
        "candidate_category_id": category_id, "candidate_product_id": product_id,
    }


def label(path, *, post_id="ig:Abcde12", business_id=7, decision="approve", category_id=2,
          product_id=3, audit=False, result="human_review", score=0.55, reason="uncertain_score"):
    return save_post_label(
        path, post_id=post_id, business_id=business_id, decision=decision,
        category_id=category_id, product_id=product_id, operator_id=19,
        snapshot=snapshot(result=result, score=score, reason=reason), audit=audit,
    )


def test_post_labels_latest_wins_and_stay_business_scoped(tmp_path):
    path = tmp_path / "human-post-labels.jsonl"
    label(path, decision="approve", category_id=2)
    label(path, decision="reject", category_id=None, product_id=None)
    label(path, business_id=8, decision="approve", category_id=9)

    all_labels = load_post_labels(path)
    only_seven = load_post_labels(path, business_id=7)

    assert all_labels[("7", "ig:Abcde12")]["decision"] == "reject"
    assert all_labels[("8", "ig:Abcde12")]["category_id"] == "9"
    assert list(only_seven) == [("7", "ig:Abcde12")]


def test_post_label_rejects_invalid_values_and_approval_without_category(tmp_path):
    path = tmp_path / "human-post-labels.jsonl"
    with pytest.raises(ValueError):
        label(path, decision="skip")
    with pytest.raises(ValueError):
        label(path, category_id=None)
    with pytest.raises(ValueError):
        save_post_label(
            path, post_id="javascript:alert(1)", business_id=7, decision="reject",
            category_id=None, product_id=None, operator_id=19, snapshot=snapshot(),
        )
    assert not path.exists()


def test_post_label_load_tolerates_malformed_rows_and_keeps_append_only_history(tmp_path):
    path = tmp_path / "human-post-labels.jsonl"
    label(path, decision="approve", category_id=2)
    original_line_count = len(path.read_text(encoding="utf-8").splitlines())
    with path.open("a", encoding="utf-8") as stream:
        stream.write("{bad json\n[]\n")
    label(path, decision="reject", category_id=None, product_id=None)

    loaded = load_post_labels(path, business_id=7)

    assert loaded[("7", "ig:Abcde12")]["decision"] == "reject"
    assert len(path.read_text(encoding="utf-8").splitlines()) == original_line_count + 3


def test_pending_reviews_are_business_scoped_newest_first_paginated_and_skip_is_noop(tmp_path):
    queue = tmp_path / "needs-review.jsonl"
    labels = tmp_path / "human-post-labels.jsonl"
    rows = [post("Abcde12"), post("Fghij34", result="confident_match", reason="matched", audit=True),
            post("Klmno56", business_id=8)]
    queue.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")

    newest, total = pending_post_reviews(queue, labels, business_id=7, limit=1)
    older, _ = pending_post_reviews(queue, labels, business_id=7, limit=1, offset=1)
    again, _ = pending_post_reviews(queue, labels, business_id=7, limit=10)

    assert total == 2
    assert newest[0]["post_id"] == "ig:Fghij34"
    assert newest[0]["audit"] is True
    assert older[0]["post_id"] == "ig:Abcde12"
    assert [row["post_id"] for row in again] == ["ig:Fghij34", "ig:Abcde12"]


def test_pending_queue_filters_and_labeling_removes_only_that_business_item(tmp_path):
    queue = tmp_path / "needs-review.jsonl"
    labels = tmp_path / "human-post-labels.jsonl"
    row = post("Abcde12", reason="ambiguous_category")
    queue.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert pending_post_reviews(queue, labels, business_id=7, reason_code="caption_too_short")[1] == 0
    assert pending_post_reviews(queue, labels, business_id=7, category_id=2)[1] == 1
    label(labels, business_id=8)
    assert pending_post_reviews(queue, labels, business_id=7)[1] == 1
    label(labels, business_id=7)
    assert pending_post_reviews(queue, labels, business_id=7)[1] == 0


def test_audit_sample_is_deterministic_capped_and_does_not_mutate_analyzer_rows():
    posts = [post(f"Code{index:02d}", result="confident_match", reason="matched", audit=False)
             for index in range(20)]
    original = json.dumps(posts, sort_keys=True)

    first = sample_confident_audit_posts(posts, sample_rate=1, max_items=4, seed=42)
    second = sample_confident_audit_posts(posts, sample_rate=1, max_items=4, seed=42)

    assert [row["id"] for row in first] == [row["id"] for row in second]
    assert len(first) == 4
    assert all(row["metadata"]["audit"] for row in first)
    assert all(row["metadata"]["category_decision"]["result"] == "confident_match" for row in first)
    assert json.dumps(posts, sort_keys=True) == original


def test_post_report_denominators_accuracy_category_disagreement_and_zero_edges():
    analyzed = [
        post("Abcde12", result="confident_match", score=0.9, reason="matched", category_id=2),
        post("Fghij34", result="human_review", score=0.6, reason="ambiguous_category", category_id=2),
        post("Klmno56", result="human_review", score=0.4, reason="caption_too_short", category_id=None),
        post("Pqrst78", result="rejected", score=0, reason="no_matching_evidence", category_id=None),
    ]
    labels_path_labels = {}
    audit = label_from_snapshot("ig:Abcde12", "confident_match", 0.9, "matched", 2, True, 2)
    review_approve = label_from_snapshot("ig:Fghij34", "human_review", 0.6, "ambiguous_category", 2, False, 8)
    review_reject = label_from_snapshot("ig:Klmno56", "human_review", 0.4, "caption_too_short", None, False, None)
    for item in (audit, review_approve, review_reject):
        labels_path_labels[("7", item["post_id"])] = item

    report = build_post_report(analyzed, labels_path_labels, business_id=7)
    empty = build_post_report([], {}, business_id=7)

    assert report["denominators"] == {
        "N_posts_analyzed": 4, "N_confident": 1, "N_review": 2,
        "N_review_labeled": 2, "N_audit_labeled": 1,
    }
    assert report["confident_match_agreement"]["percent"] == 100
    assert report["confident_match_agreement"]["denominator"] == 1
    assert report["review_band_approve_rate"]["percent"] == 50
    assert report["review_band_approve_rate"]["denominator"] == 2
    assert report["category_match"]["different_category"]["numerator"] == 1
    assert report["category_match"]["different_category"]["denominator"] == 2
    assert report["reason_breakdown"]["ambiguous_category"]["N_approve"] == 1
    assert report["threshold_tuning"] == "report_only_no_automatic_threshold_change"
    assert empty["confident_match_agreement"]["percent"] is None
    assert empty["review_band_approve_rate"]["denominator"] == 0
    assert all(bucket["human_approve_rate"]["percent"] is None for bucket in empty["score_buckets"])


def test_retention_counts_then_purges_old_review_and_label_rows(tmp_path):
    import datetime

    queue = tmp_path / "needs-review.jsonl"
    labels = tmp_path / "human-post-labels.jsonl"
    queue.write_text("\n".join((
        json.dumps({"id": "ig:OldAbc1", "metadata": {"review_queued_at": "2025-01-01T00:00:00+00:00"}}),
        json.dumps({"id": "ig:NewAbc1", "metadata": {"review_queued_at": "2026-10-22T00:00:00+00:00"}}),
    )) + "\n", encoding="utf-8")
    labels.write_text("\n".join((
        json.dumps({"post_id": "ig:OldAbc1", "timestamp": "2025-01-01T00:00:00+00:00"}),
        json.dumps({"post_id": "ig:NewAbc1", "timestamp": "2026-10-22T00:00:00+00:00"}),
    )) + "\n", encoding="utf-8")
    cutoff = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)

    preview = purge_post_review_data(tmp_path, cutoff, delete=False)
    deleted = purge_post_review_data(tmp_path, cutoff, delete=True)

    assert preview == {"instagram_post_review_queue": 1, "instagram_post_human_labels": 1}
    assert deleted == preview
    assert [json.loads(line)["id"] for line in queue.read_text(encoding="utf-8").splitlines()] == ["ig:NewAbc1"]
    assert [json.loads(line)["post_id"] for line in labels.read_text(encoding="utf-8").splitlines()] == ["ig:NewAbc1"]


def label_from_snapshot(post_id, result, score, reason, candidate_category, audit, chosen_category):
    return {
        "post_id": post_id, "business_id": "7", "decision": "approve" if chosen_category else "reject",
        "category_id": str(chosen_category) if chosen_category else None,
        "product_id": None, "operator_id": "19", "timestamp": "2026-10-23T10:00:00+00:00",
        "analyzer_snapshot": snapshot(result, score, reason, candidate_category, None),
        "audit": audit,
    }
