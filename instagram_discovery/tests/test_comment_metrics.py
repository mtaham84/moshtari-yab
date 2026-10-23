import json

import pytest

from instagram_discovery.comment_metrics import (
    PASSING_LABEL, build_comment_report, load_labels, pending_human_labels,
    save_human_label,
)


def _row(index, *, business="42", prefilter="eligible", status="not a buyer", score=0.2, user=None):
    return {
        "id": f"comment-{index}", "business_id": business,
        "commenter_id": user, "commenter_username": f"user{user or index}",
        "prefilter": {"status": prefilter},
        "jev": {"status": status, "score": score} if prefilter == "eligible" else None,
    }


def test_report_percentages_have_correct_denominators_and_unique_users():
    rows = [_row(i, prefilter="eligible" if i < 90 else "set_aside",
                 status="probably a buyer" if i < 27 else "not a buyer",
                 score=0.9 if i < 27 else 0.2, user=i % 20)
            for i in range(120)]

    report = build_comment_report(rows, threshold=0.7, business_id="42")

    assert report["denominators"] == {"N_total": 120, "N_eval": 90, "N_pass": 27}
    assert report["shares"]["evaluated_comments"] == {
        "label": PASSING_LABEL, "numerator": 27, "denominator": 90,
        "value": 0.3, "percent": 30.0,
    }
    assert report["shares"]["all_received_comments"]["percent"] == 22.5
    assert report["shares"]["evaluation_coverage"]["percent"] == 75.0
    assert report["comments"]["total_received"]["unique_users"]["unique_users"] == 20
    assert report["comments"]["passed_threshold"]["unique_users"]["unique_users"] == 20


def test_report_handles_zero_denominators_without_division_errors():
    report = build_comment_report([], labels={})

    assert report["denominators"] == {"N_total": 0, "N_eval": 0, "N_pass": 0}
    assert report["shares"]["evaluated_comments"]["value"] is None
    assert report["shares"]["evaluated_comments"]["percent"] is None
    assert report["shares"]["all_received_comments"]["denominator"] == 0
    assert report["shares"]["evaluation_coverage"]["percent"] is None
    assert report["human_label_calibration"]["accuracy"]["percent"] is None
    assert report["human_label_calibration"]["recommended_threshold"] is None


def test_human_labels_measure_accuracy_and_suggest_threshold():
    rows = [
        _row(1, status="probably a buyer", score=0.9),
        _row(2, status="probably a buyer", score=0.55),
        _row(3, status="not a buyer", score=0.8),
        _row(4, status="not a buyer", score=0.2),
    ]
    labels = {
        "comment-1": {"label": "approve"},
        "comment-2": {"label": "approve"},
        "comment-3": {"label": "reject"},
        "comment-4": {"label": "reject"},
    }

    report = build_comment_report(rows, threshold=0.7, labels=labels)
    calibration = report["human_label_calibration"]

    assert calibration["labeled_comments"] == 4
    assert calibration["accuracy"]["numerator"] == 3
    assert calibration["accuracy"]["denominator"] == 4
    assert calibration["accuracy"]["percent"] == 75
    assert calibration["recommended_threshold"] == 0.55


def test_human_labels_are_business_scoped_and_only_evaluable_rows_are_pending(tmp_path):
    rows = [_row(1, business="42"), _row(2, business="99"),
            _row(3, business="42", prefilter="set_aside")]
    labels_file = tmp_path / "human-labels.jsonl"
    save_human_label(labels_file, comment_id="comment-1", business_id=42,
                     label="approve", operator_id=5)
    labels = load_labels(labels_file, business_id=42)

    assert labels["comment-1"]["operator_id"] == "5"
    assert [row["id"] for row in pending_human_labels(rows, labels, business_id=42)] == []
    assert [row["id"] for row in pending_human_labels(rows, {}, business_id=42)] == ["comment-1"]
    assert json.loads(labels_file.read_text(encoding="utf-8"))["label"] == "approve"


def test_invalid_threshold_and_label_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        build_comment_report([], threshold=1.1)
    with pytest.raises(ValueError):
        save_human_label(tmp_path / "labels.jsonl", comment_id="c", business_id=1,
                         label="maybe", operator_id=1)
