"""Business-scoped human review, audit sampling, and accuracy metrics for posts."""
from __future__ import annotations

import copy
import json
import math
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from file_lock import exclusive_file_lock
from shared.instagram import PageRefError, parse_post_ref
from .post_label_store import PostLabelStore

POST_DECISIONS = frozenset({"approve", "reject"})
ANALYZER_RESULTS = frozenset({"confident_match", "human_review", "rejected"})
REASON_CODES = frozenset({
    "matched", "caption_too_short", "uncertain_score", "ambiguous_category",
    "no_matching_evidence",
})
_POST_ID = re.compile(r"^ig:([A-Za-z0-9_-]{5,32})$")


def post_review_paths(output_dir: str | Path | None = None) -> tuple[Path, Path, Path]:
    """Return analyzed posts, pending queue, and append-only post-label paths."""
    configured = Path(output_dir or os.getenv("IG_COLLECT_OUTPUT_DIR", "data/ig_collected"))
    return (configured / "latest.jsonl", configured / "needs-review.jsonl",
            configured / "human-post-labels.jsonl")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def analyzer_snapshot(post: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only classifier fields needed to explain a later operator label."""
    metadata = post.get("metadata")
    decision = metadata.get("category_decision") if isinstance(metadata, Mapping) else None
    if not isinstance(decision, Mapping):
        raise ValueError("post has no analyzer decision")
    result = decision.get("result")
    score = _finite_score(decision.get("score"))
    reason = decision.get("reason")
    reason_code = reason.get("code") if isinstance(reason, Mapping) else None
    if (not isinstance(result, str) or result not in ANALYZER_RESULTS or score is None
            or not isinstance(reason_code, str) or reason_code not in REASON_CODES):
        raise ValueError("post analyzer decision is invalid")
    return {
        "result": result,
        "score": score,
        "reason_code": reason_code,
        "candidate_category_id": _optional_identifier(decision.get("category_candidate_id")),
        "candidate_product_id": _optional_identifier(decision.get("product_candidate_id")),
    }


def _positive_identifier(value: Any, *, field: str) -> str:
    raw = str(value or "").strip()
    if not raw.isascii() or not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{field} must be a positive integer identifier")
    return str(int(raw))


def _optional_identifier(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return _positive_identifier(value, field="identifier")
    except ValueError:
        return None


def _finite_score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return score if math.isfinite(score) and 0 <= score <= 1 else None


def _validated_snapshot(value: Mapping[str, Any]) -> dict[str, Any]:
    result = value.get("result")
    score = _finite_score(value.get("score"))
    reason_code = value.get("reason_code")
    if result not in ANALYZER_RESULTS or score is None or reason_code not in REASON_CODES:
        raise ValueError("analyzer snapshot is invalid")
    category = value.get("candidate_category_id")
    product = value.get("candidate_product_id")
    return {
        "result": result,
        "score": score,
        "reason_code": reason_code,
        "candidate_category_id": _positive_identifier(category, field="candidate_category_id")
        if category not in (None, "") else None,
        "candidate_product_id": _positive_identifier(product, field="candidate_product_id")
        if product not in (None, "") else None,
    }


def _valid_label(row: Any) -> bool:
    if not isinstance(row, Mapping):
        return False
    try:
        _positive_identifier(row.get("business_id"), field="business_id")
        _positive_identifier(row.get("operator_id"), field="operator_id")
        post_id = str(row.get("post_id") or "")
        if not _POST_ID.fullmatch(post_id) or row.get("decision") not in POST_DECISIONS:
            return False
        if row.get("decision") == "approve" and not _positive_identifier(row.get("category_id"), field="category_id"):
            return False
        if row.get("decision") == "reject" and row.get("category_id") not in (None, ""):
            _positive_identifier(row.get("category_id"), field="category_id")
        if row.get("product_id") not in (None, ""):
            _positive_identifier(row.get("product_id"), field="product_id")
        _validated_snapshot(row.get("analyzer_snapshot", {}))
        timestamp = str(row.get("timestamp") or "")
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None and isinstance(row.get("audit"), bool)
    except (TypeError, ValueError):
        return False


def load_post_labels(
    path: str | Path, *, business_id: str | int | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Load latest valid labels keyed by (business id, post id); ignore malformed rows."""
    target = Path(path)
    selected_business = (
        _positive_identifier(business_id, field="business_id") if business_id is not None else None
    )
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _read_jsonl(target):
        if not _valid_label(row):
            continue
        row_business = str(int(str(row["business_id"])))
        if selected_business is not None and row_business != selected_business:
            continue
        key = (row_business, row["post_id"])
        latest[key] = dict(row)
    return latest


def save_post_label(
    path: str | Path,
    *,
    post_id: str,
    business_id: str | int,
    decision: str,
    category_id: str | int | None,
    product_id: str | int | None,
    operator_id: str | int,
    snapshot: Mapping[str, Any],
    audit: bool = False,
) -> dict[str, Any]:
    """Append a validated operator label without changing analyzer or queue files."""
    business = _positive_identifier(business_id, field="business_id")
    operator = _positive_identifier(operator_id, field="operator_id")
    if not _POST_ID.fullmatch(str(post_id)):
        raise ValueError("post_id is invalid")
    if not isinstance(decision, str) or decision not in POST_DECISIONS:
        raise ValueError("decision must be approve or reject")
    category = _positive_identifier(category_id, field="category_id") if category_id not in (None, "") else None
    product = _positive_identifier(product_id, field="product_id") if product_id not in (None, "") else None
    if decision == "approve" and category is None:
        raise ValueError("category_id is required for approval")
    if not isinstance(audit, bool):
        raise ValueError("audit must be boolean")
    clean_snapshot = _validated_snapshot(snapshot)
    row = {
        "post_id": str(post_id), "business_id": business, "decision": decision,
        "category_id": category, "product_id": product, "operator_id": operator,
        "timestamp": datetime.now(timezone.utc).isoformat(), "analyzer_snapshot": clean_snapshot,
        "audit": audit,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(target.with_suffix(target.suffix + ".lock")):
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return row


def post_business_id(post: Mapping[str, Any]) -> str | None:
    metadata = post.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    for key in ("suggested_target", "collection_source"):
        source = metadata.get(key)
        if isinstance(source, Mapping) and source.get("business_id") not in (None, ""):
            try:
                return _positive_identifier(source["business_id"], field="business_id")
            except ValueError:
                return None
    return None


def post_decision(post: Mapping[str, Any]) -> Mapping[str, Any] | None:
    metadata = post.get("metadata")
    decision = metadata.get("category_decision") if isinstance(metadata, Mapping) else None
    return decision if isinstance(decision, Mapping) else None


def is_audit_post(post: Mapping[str, Any]) -> bool:
    decision = post_decision(post)
    metadata = post.get("metadata")
    audit = metadata.get("audit") if isinstance(metadata, Mapping) else post.get("audit")
    return bool(audit) and bool(decision and decision.get("result") == "confident_match")


def _queue_row(post: Mapping[str, Any], audit: bool) -> dict[str, Any] | None:
    identifier = str(post.get("id") or "")
    if not _POST_ID.fullmatch(identifier):
        return None
    decision = post_decision(post)
    if not decision:
        return None
    if audit and decision.get("result") != "confident_match":
        return None
    if not audit and decision.get("result") != "human_review":
        return None
    metadata = post.get("metadata")
    meta = metadata if isinstance(metadata, Mapping) else {}
    reason = decision.get("reason") if isinstance(decision.get("reason"), Mapping) else {}
    evidence = reason.get("evidence")
    evidence_items: list[dict[str, str]] = []
    if isinstance(evidence, Mapping):
        evidence_items = [
            {"label": str(key), "text": str(value)[:500]}
            for key, value in evidence.items() if value not in (None, "", [], {})
        ]
    collection_source = meta.get("collection_source")
    source = collection_source if isinstance(collection_source, Mapping) else {}
    post_url = ""
    try:
        shortcode = parse_post_ref(str(post.get("url") or ""))
    except PageRefError:
        pass
    else:
        if identifier == f"ig:{shortcode}":
            post_url = f"https://www.instagram.com/p/{shortcode}/"
    hashtags_value = meta.get("hashtags") or []
    hashtags = hashtags_value if isinstance(hashtags_value, list) else []
    return {
        "post_id": identifier,
        "business_id": post_business_id(post),
        "caption": str(post.get("text") or "")[:500],
        "hashtags": [str(value)[:80] for value in hashtags[:50]],
        "post_url": post_url,
        "source_hashtag": str(source.get("value") or ""),
        "candidate_category_id": _optional_identifier(decision.get("category_candidate_id")),
        "candidate_category": decision.get("category_candidate"),
        "candidate_product_id": _optional_identifier(decision.get("product_candidate_id")),
        "candidate_product": decision.get("product_candidate"),
        "score": _finite_score(decision.get("score")),
        "reason_code": str(reason.get("code") or ""),
        "evidence": evidence_items,
        "knowledge_source": decision.get("knowledge_source") or {"kind": "unavailable"},
        "audit": audit,
        "analyzer_snapshot": _safe_snapshot(decision),
    }


def _safe_snapshot(decision: Mapping[str, Any]) -> dict[str, Any]:
    reason = decision.get("reason") if isinstance(decision.get("reason"), Mapping) else {}
    return {
        "result": decision.get("result"), "score": _finite_score(decision.get("score")),
        "reason_code": reason.get("code"),
        "candidate_category_id": _optional_identifier(decision.get("category_candidate_id")),
        "candidate_product_id": _optional_identifier(decision.get("product_candidate_id")),
    }


def pending_post_reviews(
    queue_path: str | Path,
    labels_path: str | Path,
    *,
    business_id: str | int,
    limit: int = 25,
    offset: int = 0,
    reason_code: str | None = None,
    category_id: str | int | None = None,
    post_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """Return newest unlabelled review and audit rows for one business only."""
    business = _positive_identifier(business_id, field="business_id")
    if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
        raise ValueError("pagination is out of range")
    if post_id is not None and not _POST_ID.fullmatch(post_id):
        raise ValueError("post_id is invalid")
    labels = load_post_labels(labels_path, business_id=business)
    wanted_category = _optional_identifier(category_id)
    pending: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in reversed(_read_jsonl(Path(queue_path))):
        post_id = str(raw.get("id") or "")
        if post_id in seen or not _POST_ID.fullmatch(post_id):
            continue
        if post_business_id(raw) != business or (business, post_id) in labels:
            continue
        seen.add(post_id)
        raw_metadata = raw.get("metadata")
        metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        decision = post_decision(raw)
        if not decision:
            continue
        audit = is_audit_post(raw)
        if decision.get("result") != "human_review" and not audit:
            continue
        reason = decision.get("reason") if isinstance(decision.get("reason"), Mapping) else {}
        current_reason = str(reason.get("code") or "")
        current_category_id = _optional_identifier(decision.get("category_candidate_id"))
        if reason_code and current_reason != reason_code:
            continue
        if wanted_category and current_category_id != wanted_category:
            continue
        if post_id is not None and post_id != str(raw.get("id") or ""):
            continue
        item = _queue_row(raw, audit)
        if item is None:
            continue
        item["queued_at"] = str(
            raw.get("review_queued_at") or metadata.get("review_queued_at") or raw.get("created_at") or ""
        )
        pending.append(item)
    total = len(pending)
    return pending[offset:offset + limit], total


def sample_confident_audit_posts(
    posts: Iterable[Mapping[str, Any]], *, sample_rate: float, max_items: int, seed: int,
) -> list[dict[str, Any]]:
    """Choose a reproducible capped sample of confident posts for blind operator review."""
    try:
        rate = float(sample_rate)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("sample_rate must be between 0 and 1") from exc
    if not math.isfinite(rate) or not 0 <= rate <= 1 or not 0 <= max_items <= 100:
        raise ValueError("audit sampling configuration is out of range")
    if isinstance(max_items, bool) or not isinstance(max_items, int):
        raise ValueError("max_items must be an integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    unique: dict[tuple[str, str], Mapping[str, Any]] = {}
    for post in posts:
        decision = post_decision(post)
        post_id = str(post.get("id") or "")
        business = post_business_id(post)
        if decision and decision.get("result") == "confident_match" and business and _POST_ID.fullmatch(post_id):
            unique[(business, post_id)] = post
    candidates = [unique[key] for key in sorted(unique)]
    count = min(max_items, int(len(candidates) * rate))
    if count == 0:
        return []
    selected = random.Random(seed).sample(candidates, count)
    result: list[dict[str, Any]] = []
    for post in selected:
        queued = copy.deepcopy(dict(post))
        metadata = queued.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            queued["metadata"] = metadata
        metadata["review_queue_status"] = "queued"
        metadata["human_review_required"] = True
        metadata["audit"] = True
        queued["audit"] = True
        queued["review_queued_at"] = datetime.now(timezone.utc).isoformat()
        result.append(queued)
    return result


def _ratio(numerator: int, denominator: int, label: str) -> dict[str, Any]:
    return {"label": label, "numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None,
            "percent": numerator * 100 / denominator if denominator else None}


def build_post_report(
    analyzed_posts: Iterable[Mapping[str, Any]],
    label_store: PostLabelStore,
    *,
    business_id: str | int,
) -> dict[str, Any]:
    """Report classifier review and blind audit agreement with explicit denominators."""
    business = _positive_identifier(business_id, field="business_id")
    posts: dict[str, Mapping[str, Any]] = {}
    for post in analyzed_posts:
        post_id = str(post.get("id") or "")
        if post_business_id(post) == business and _POST_ID.fullmatch(post_id) and post_decision(post):
            posts[post_id] = post
    confident = {key: post for key, post in posts.items()
                 if post_decision(post).get("result") == "confident_match"}
    review = {key: post for key, post in posts.items()
              if post_decision(post).get("result") == "human_review"}
    labels = label_store.load(business_id=business)
    selected_labels = {
        post_id: label for (label_business, post_id), label in labels.items()
        if label_business == business and post_id in posts and _valid_label(label)
    }
    review_labels = {post_id: label for post_id, label in selected_labels.items()
                     if label["analyzer_snapshot"]["result"] == "human_review"}
    audit_labels = {post_id: label for post_id, label in selected_labels.items()
                    if label["audit"] and label["analyzer_snapshot"]["result"] == "confident_match"}
    review_approved = sum(label["decision"] == "approve" for label in review_labels.values())
    review_rejected = sum(label["decision"] == "reject" for label in review_labels.values())
    audit_agreed = sum(label["decision"] == "approve" for label in audit_labels.values())
    candidate_approved = [label for label in selected_labels.values()
                          if label["decision"] == "approve"
                          and label["analyzer_snapshot"].get("candidate_category_id")]
    category_agreements = sum(str(label.get("category_id")) ==
                              str(label["analyzer_snapshot"].get("candidate_category_id"))
                              for label in candidate_approved)
    category_disagreements = len(candidate_approved) - category_agreements
    reasons: dict[str, dict[str, int]] = {}
    for post in posts.values():
        decision = post_decision(post)
        reason = decision.get("reason") if isinstance(decision.get("reason"), Mapping) else {}
        code = str(reason.get("code") or "unknown")
        entry = reasons.setdefault(code, {"N_posts": 0, "N_labeled": 0, "N_approve": 0, "N_reject": 0})
        entry["N_posts"] += 1
        label = selected_labels.get(str(post.get("id")))
        if label and label["analyzer_snapshot"]["result"] == decision.get("result"):
            entry["N_labeled"] += 1
            entry["N_approve"] += int(label["decision"] == "approve")
            entry["N_reject"] += int(label["decision"] == "reject")
    buckets = ((0.0, 0.25, False), (0.25, 0.5, False),
               (0.5, 0.75, False), (0.75, 1.0, True))
    score_buckets = []
    for lower, upper, inclusive_upper in buckets:
        in_bucket = [label for label in selected_labels.values()
                     if lower <= label["analyzer_snapshot"]["score"]
                     and (label["analyzer_snapshot"]["score"] <= upper if inclusive_upper
                          else label["analyzer_snapshot"]["score"] < upper)]
        approved = sum(label["decision"] == "approve" for label in in_bucket)
        score_buckets.append({
            "range": ({"min_inclusive": lower, "max_inclusive": upper} if inclusive_upper
                      else {"min_inclusive": lower, "max_exclusive": upper}),
            "labeled_denominator": len(in_bucket), "approved": approved, "rejected": len(in_bucket) - approved,
            "human_approve_rate": _ratio(approved, len(in_bucket), "operator approvals / labeled posts in score bucket"),
        })
    return {
        "business_id": business,
        "denominators": {
            "N_posts_analyzed": len(posts), "N_confident": len(confident), "N_review": len(review),
            "N_review_labeled": len(review_labels), "N_audit_labeled": len(audit_labels),
        },
        "review_labels": {"approve": review_approved, "reject": review_rejected,
                          "total": len(review_labels)},
        "confident_match_agreement": _ratio(
            audit_agreed, len(audit_labels), "operator approvals / labeled confident-match audit posts",
        ),
        "review_band_approve_rate": _ratio(
            review_approved, len(review_labels), "operator approvals / labeled human-review posts",
        ),
        "category_match": {
            "agreement": _ratio(category_agreements, len(candidate_approved),
                                "operator category matches / approved posts with a candidate category"),
            "different_category": _ratio(category_disagreements, len(candidate_approved),
                                         "operator category differs / approved posts with a candidate category"),
        },
        "reason_breakdown": reasons,
        "score_buckets": score_buckets,
        "threshold_tuning": "report_only_no_automatic_threshold_change",
    }


def purge_post_review_data(output_dir: str | Path, cutoff: datetime, *, delete: bool) -> dict[str, int]:
    """Count or purge stale queue and label rows for the existing retention command."""
    queue_path = Path(output_dir) / "needs-review.jsonl"
    labels_path = Path(output_dir) / "human-post-labels.jsonl"
    report: dict[str, int] = {}
    for name, path, date_field in (
        ("instagram_post_review_queue", queue_path, "review_queued_at"),
        ("instagram_post_human_labels", labels_path, "timestamp"),
    ):
        if not path.is_file():
            report[name] = 0
            continue
        kept: list[str] = []
        removed = 0
        with exclusive_file_lock(path.with_suffix(path.suffix + ".lock")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                report[name] = 0
                continue
            for line in lines:
                try:
                    item = json.loads(line)
                    if not isinstance(item, Mapping):
                        raise ValueError("record must be an object")
                    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
                    raw_timestamp = (item.get(date_field) or metadata.get(date_field)
                                     or item.get("created_at") or item.get("timestamp"))
                    timestamp = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
                    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                        raise ValueError("timestamp must include timezone")
                    stale = timestamp.astimezone(timezone.utc) < cutoff.astimezone(timezone.utc)
                except (json.JSONDecodeError, TypeError, ValueError, OverflowError):
                    stale = False
                if stale:
                    removed += 1
                else:
                    kept.append(line)
            report[name] = removed
            if delete and removed:
                temporary = path.with_suffix(path.suffix + ".tmp")
                temporary.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
                os.replace(temporary, path)
    return report
