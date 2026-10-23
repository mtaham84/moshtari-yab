"""Select and prioritize Instagram posts before the bounded comment-read stage."""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence, TypeAlias

from instagram_discovery.analyzer import normalize_text
from instagram_discovery.post_label_store import PostLabelStore
from .backends.base import InstagramPost


CommentCandidate: TypeAlias = tuple[InstagramPost, str, dict[str, Any]]

_INQUIRY_PHRASES = (
    "پیام بده", "پیام دهید", "دایرکت", "برای اطلاعات بیشتر", "اطلاعات بیشتر",
    "سوال دارید", "سؤال دارید", "بپرسید", "مشاوره", "استعلام",
)
_PRODUCT_INTRO_PHRASES = (
    "معرفی محصول", "محصول جدید", "موجود است", "موجوده", "برای خرید",
    "سفارش", "فروش", "محصول",
)
_HIDDEN_PRICE_PHRASES = (
    "قیمت در دایرکت", "دایرکت برای قیمت", "برای قیمت", "استعلام قیمت",
    "قیمت دایرکت", "قیمت رو دایرکت", "قیمت را دایرکت",
)


def seller_post_priority(
    caption: str,
    *,
    analysis: Mapping[str, Any] | None = None,
    category_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return an explainable, deterministic priority score for seller posts.

    The score is a queue-ordering heuristic, not a probability or a lead verdict.
    """
    analysis = analysis or {}
    category_decision = category_decision or {}
    normalized = normalize_text(caption)

    product_evidence = _text_values(analysis.get("product_language_signals"))
    decision_reason = category_decision.get("reason")
    decision_evidence = decision_reason.get("evidence", {}) if isinstance(decision_reason, Mapping) else {}
    if isinstance(decision_evidence, Mapping):
        product_evidence.extend(_text_values(decision_evidence.get("product_terms")))
    product_name = category_decision.get("product") or category_decision.get("product_candidate")
    if isinstance(product_name, str) and product_name.strip():
        product_evidence.append(product_name.strip())
    if not product_evidence:
        product_evidence.extend(_matching_phrases(normalized, _PRODUCT_INTRO_PHRASES))
    product_evidence = _unique(product_evidence)

    hidden_price_evidence = _text_values(analysis.get("price_hidden_signals"))
    if analysis.get("price_status") != "explicit" and not hidden_price_evidence:
        hidden_price_evidence = _matching_phrases(normalized, _HIDDEN_PRICE_PHRASES)
    hidden_price_evidence = _unique(hidden_price_evidence)

    inquiry_evidence = _text_values(analysis.get("comment_bait_signals"))
    inquiry_evidence.extend(_matching_phrases(normalized, _INQUIRY_PHRASES))
    inquiry_evidence = _unique(inquiry_evidence)

    signals: list[dict[str, Any]] = []
    score = 0
    for name, weight, evidence in (
        ("product_introduction", 1, product_evidence),
        ("hidden_price", 3, hidden_price_evidence),
        ("invitation_to_inquire", 2, inquiry_evidence),
    ):
        if evidence:
            score += weight
            signals.append({"name": name, "weight": weight, "evidence": evidence})

    return {
        "result": "prioritized" if score else "standard_priority",
        "category": category_decision.get("category") or category_decision.get("category_candidate"),
        "product": product_name,
        "score": score,
        "reason": {"signals": signals, "caption_excerpt": caption[:500]},
        "knowledge_source": category_decision.get("knowledge_source") or {"kind": "caption_heuristics"},
    }


def select_comment_candidates(
    candidates: Sequence[CommentCandidate],
    *,
    since: datetime,
    post_label_store: PostLabelStore | None = None,
) -> list[CommentCandidate]:
    """Keep recent confident or operator-approved posts and prioritize seller posts.

    Hashtag posts require a confident match to the exact seeded category. Account
    page candidates retain their existing seller-post eligibility rules. A
    business-scoped operator rejection blocks the post; approval may admit an
    uncertain hashtag post without altering its analyzer decision.
    """
    labels = post_label_store.load() if post_label_store is not None else {}
    selected: list[CommentCandidate] = []
    for post, seed_key, record in candidates:
        if not _is_recent(post.created_at, since):
            continue
        business_id = _record_business_id(record)
        label = labels.get((business_id, f"ig:{post.shortcode}")) if business_id else None
        if label and label.get("decision") == "reject":
            continue
        candidate_record = record
        if label and label.get("decision") == "approve":
            candidate_record = _with_operator_target(record, label, business_id)
        elif seed_key.startswith("hashtag:") and not _has_confident_target_match(record):
            continue
        selected.append((post, seed_key, candidate_record))

    return sorted(
        selected,
        key=lambda candidate: (
            -_priority_score(candidate[2]),
            -_timestamp(candidate[0].created_at).timestamp(),
        ),
    )


def _record_business_id(record: Mapping[str, Any]) -> str:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return ""
    for field in ("suggested_target", "collection_source"):
        target = metadata.get(field)
        if isinstance(target, Mapping) and target.get("business_id") not in (None, ""):
            value = str(target["business_id"]).strip()
            if value.isascii() and value.isdigit() and int(value) > 0:
                return str(int(value))
    return ""


def _with_operator_target(
    record: dict[str, Any], label: Mapping[str, Any], business_id: str,
) -> dict[str, Any]:
    """Carry operator provenance to comment evaluation without rewriting analysis."""
    selected = copy.deepcopy(record)
    category_id = str(label.get("category_id") or "")
    product_id = str(label.get("product_id") or "") or None
    target = {
        "source": "operator",
        "business_id": business_id,
        "category_id": category_id,
        "product_id": product_id,
        "score": None,
    }
    metadata = selected.setdefault("metadata", {})
    metadata["operator_post_label"] = {
        "decision": "approve",
        "category_id": category_id,
        "product_id": product_id,
        "audit": bool(label.get("audit")),
    }
    metadata["suggested_target"] = target
    parent = metadata.get("parent_post")
    if isinstance(parent, dict):
        parent["suggested_target"] = target
    return selected


def _has_confident_target_match(record: Mapping[str, Any]) -> bool:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return False
    decision = metadata.get("category_decision")
    if not isinstance(decision, Mapping) or decision.get("result") != "confident_match":
        return False
    target_category_id = decision.get("source_category_id")
    matched_category_id = decision.get("category_id")
    return target_category_id is not None and str(target_category_id) == str(matched_category_id)


def _priority_score(record: Mapping[str, Any]) -> int:
    metadata = record.get("metadata")
    if not isinstance(metadata, Mapping):
        return 0
    priority = metadata.get("comment_priority")
    if not isinstance(priority, Mapping):
        return 0
    try:
        return max(0, int(priority.get("score", 0)))
    except (TypeError, ValueError):
        return 0


def _is_recent(value: Any, since: datetime) -> bool:
    timestamp = _timestamp(value)
    return timestamp is not None and timestamp > since


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _text_values(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _matching_phrases(normalized_caption: str, phrases: Sequence[str]) -> list[str]:
    return [phrase for phrase in phrases if normalize_text(phrase) in normalized_caption]


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
