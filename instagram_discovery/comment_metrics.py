"""Denominator-explicit Instagram comment metrics and human-label calibration."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping

from file_lock import exclusive_file_lock

PASSING_LABEL = "share of comments that passed the selection threshold"


def _configured_threshold() -> float:
    try:
        value = float(os.getenv("IG_JEV_SELECTION_THRESHOLD", "0.7"))
    except ValueError:
        return 0.7
    return value if 0.0 <= value <= 1.0 else 0.7


DEFAULT_JEV_THRESHOLD = _configured_threshold()


def _ratio(numerator: int, denominator: int, *, label: str) -> dict[str, Any]:
    return {
        "label": label,
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "percent": numerator * 100 / denominator if denominator else None,
    }


def _rows(rows: Iterable[Mapping[str, Any]], business_id: str | int | None) -> list[Mapping[str, Any]]:
    output = []
    for row in rows:
        if business_id is None or str(row.get("business_id") or "") == str(business_id):
            output.append(row)
    return output


def _user_key(row: Mapping[str, Any]) -> str | None:
    raw_user_id = row.get("commenter_id")
    user_id = str(raw_user_id).strip() if raw_user_id is not None else ""
    if user_id:
        return f"id:{user_id}"
    username = str(row.get("commenter_username") or "").strip().lstrip("@").casefold()
    return f"username:{username}" if username else None


def _is_evaluable(row: Mapping[str, Any]) -> bool:
    prefilter = row.get("prefilter")
    return isinstance(prefilter, Mapping) and prefilter.get("status") == "eligible" and isinstance(row.get("jev"), Mapping)


def _is_passing(row: Mapping[str, Any], threshold: float) -> bool:
    decision = row.get("jev")
    if not isinstance(decision, Mapping):
        return False
    try:
        score = float(decision.get("score"))
    except (TypeError, ValueError):
        return False
    return decision.get("status") == "probably a buyer" and score >= threshold


def _unique_users(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    identities = {_user_key(row) for row in rows}
    identities.discard(None)
    return {"unique_users": len(identities), "comments_without_user_identity": sum(_user_key(row) is None for row in rows)}


def _human_calibration(
    evaluable: list[Mapping[str, Any]], labels: Mapping[str, Mapping[str, Any]], threshold: float,
) -> dict[str, Any]:
    labeled = []
    for row in evaluable:
        label = labels.get(str(row.get("id") or ""), {}).get("label")
        if label in {"approve", "reject"} and isinstance(row.get("jev"), Mapping):
            labeled.append((row, label == "approve"))
    tp = fp = tn = fn = 0
    for row, approved in labeled:
        predicted = _is_passing(row, threshold)
        if predicted and approved:
            tp += 1
        elif predicted:
            fp += 1
        elif approved:
            fn += 1
        else:
            tn += 1
    correct = tp + tn
    candidates = sorted({0.0, 1.0, *(float(row["jev"]["score"]) for row, _ in labeled
                                     if _valid_score(row["jev"].get("score")))})
    sweep = []
    for candidate in candidates:
        candidate_correct = sum(_is_passing(row, candidate) == approved for row, approved in labeled)
        sweep.append({"threshold": candidate, "correct": candidate_correct, "labeled_denominator": len(labeled),
                      "accuracy": candidate_correct / len(labeled) if labeled else None})
    recommended = max(sweep, key=lambda item: (item["accuracy"], item["threshold"])) if labeled else None
    return {
        "labeled_comments": len(labeled),
        "accuracy": _ratio(correct, len(labeled), label="Jev accuracy on operator-labeled evaluable comments"),
        "confusion_matrix": {"true_positive": tp, "false_positive": fp, "true_negative": tn, "false_negative": fn},
        "threshold_sweep": sweep,
        "recommended_threshold": recommended["threshold"] if recommended else None,
    }


def _valid_score(value: Any) -> bool:
    try:
        score = float(value)
        return 0 <= score <= 1
    except (TypeError, ValueError):
        return False


def build_comment_report(
    rows: Iterable[Mapping[str, Any]], *, threshold: float = DEFAULT_JEV_THRESHOLD,
    labels: Mapping[str, Mapping[str, Any]] | None = None, business_id: str | int | None = None,
) -> dict[str, Any]:
    """Build API/UI metrics; all ratios include explicit numerator and denominator."""
    if not _valid_score(threshold):
        raise ValueError("threshold must be between 0 and 1")
    selected = _rows(rows, business_id)
    evaluable = [row for row in selected if _is_evaluable(row)]
    passing = [row for row in evaluable if _is_passing(row, threshold)]
    received_users = _unique_users(selected)
    eval_users = _unique_users(evaluable)
    pass_users = _unique_users(passing)
    labels = labels or {}
    calibration = _human_calibration(evaluable, labels, threshold)
    return {
        "scope": {"business_id": str(business_id) if business_id is not None else None,
                  "basis": "comments with matching business_id in Instagram category/hashtag seed logs"},
        "threshold": threshold,
        "threshold_configuration": "IG_JEV_SELECTION_THRESHOLD",
        "denominators": {"N_total": len(selected), "N_eval": len(evaluable), "N_pass": len(passing)},
        "comments": {
            "total_received": {"count": len(selected), "unique_users": received_users},
            "evaluable_after_prefilter": {"count": len(evaluable), "unique_users": eval_users},
            "passed_threshold": {"count": len(passing), "unique_users": pass_users},
        },
        "shares": {
            "evaluated_comments": _ratio(len(passing), len(evaluable), label=PASSING_LABEL),
            "all_received_comments": _ratio(len(passing), len(selected), label=PASSING_LABEL),
            "evaluation_coverage": _ratio(len(evaluable), len(selected), label="evaluation coverage (evaluable comments / total comments received)"),
        },
        "human_label_calibration": calibration,
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def load_labels(path: Path, *, business_id: str | int | None = None) -> dict[str, dict[str, Any]]:
    labels = {}
    for row in read_jsonl(path):
        if business_id is None or str(row.get("business_id") or "") == str(business_id):
            if row.get("label") in {"approve", "reject"} and row.get("comment_id"):
                labels[str(row["comment_id"])] = row
    return labels


def save_human_label(path: Path, *, comment_id: str, business_id: str | int, label: str,
                     operator_id: str | int) -> None:
    if label not in {"approve", "reject"}:
        raise ValueError("label must be approve or reject")
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"comment_id": comment_id, "business_id": str(business_id), "label": label,
           "operator_id": str(operator_id)}
    with exclusive_file_lock(path.with_suffix(path.suffix + ".lock")):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def reporting_paths() -> tuple[Path, Path]:
    output = Path(os.getenv("IG_COLLECT_OUTPUT_DIR", "data/ig_collected"))
    return output / "comment-evaluations.jsonl", output / "human-labels.jsonl"


def pending_human_labels(rows: Iterable[Mapping[str, Any]], labels: Mapping[str, Mapping[str, Any]],
                         *, business_id: str | int, limit: int = 50) -> list[dict[str, Any]]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    output = []
    for row in _rows(rows, business_id):
        if _is_evaluable(row) and str(row.get("id") or "") not in labels:
            output.append(dict(row))
            if len(output) >= limit:
                break
    return output
