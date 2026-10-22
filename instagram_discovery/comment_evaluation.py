"""Isolated, explainable evaluation of Instagram comments through Jev."""
from __future__ import annotations

import re
from dataclasses import dataclass
from math import isfinite
from typing import Any, Mapping, Protocol, Sequence

from .analyzer import normalize_text
from .comment_intent import classify_comment
from .comment_threshold import jev_selection_threshold


MIN_COMMENT_DECISION_LENGTH = 20


@dataclass(frozen=True)
class KnowledgeSnapshot:
    """Read-only category/product answers and indexed passages for one comment."""

    related_category: Mapping[str, Any] | None
    related_product: Mapping[str, Any] | None
    answers: Sequence[Mapping[str, Any] | str]
    indexed_information: Sequence[Mapping[str, Any] | str]
    source: Mapping[str, Any]


class InstagramKnowledgeReader(Protocol):
    """Read-only boundary for retrieving category/product and indexed knowledge."""

    def retrieve_for_comment(
        self,
        *,
        comment_text: str,
        category_id: str | None,
        product_id: str | None,
        post_context: Mapping[str, Any],
    ) -> KnowledgeSnapshot: ...


class JevCommentEvaluator(Protocol):
    """Jev adapter contract. Implementations must only evaluate, never contact users."""

    def evaluate_comment(
        self,
        *,
        comment_text: str,
        knowledge: KnowledgeSnapshot,
        post_context: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class PrefilterDecision:
    status: str
    reason: str
    evidence: tuple[str, ...]


_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_FOLLOW_FOR_FOLLOW = tuple(map(normalize_text, (
    "follow for follow", "follow4follow", "follow back", "f4f", "فالو بک",
    "فالو فالو", "فالو کن فالو کنم", "لایک و فالو",
)))
_SELF_PROMOTION = tuple(map(normalize_text, (
    "پیج من", "پیج ما", "فروشگاه من", "فروشگاه ما", "از پیج ما خرید",
    "من میفروشم", "ما میفروشیم", "موجود دارم", "موجود داریم", "سفارش بگیرید",
)))


def prefilter_comment(
    text: str,
    *,
    commenter_handle: str | None = None,
    post_author_handle: str | None = None,
) -> PrefilterDecision:
    """Set aside clear spam or self-promotion before any Jev/knowledge call."""
    normalized = normalize_text(text)
    url_match = _URL.search(text)
    if url_match:
        return PrefilterDecision("set_aside", "link_spam", (url_match.group(0),))

    for phrase in _FOLLOW_FOR_FOLLOW:
        if phrase and phrase in normalized:
            return PrefilterDecision("set_aside", "follow_for_follow", (phrase,))

    existing_intent = classify_comment(text)
    if existing_intent.label == "other" and existing_intent.cue:
        return PrefilterDecision("set_aside", "existing_comment_prefilter", (existing_intent.cue,))

    self_promotion = [phrase for phrase in _SELF_PROMOTION if phrase and phrase in normalized]
    same_author = bool(commenter_handle and post_author_handle
                       and _normalize_handle(commenter_handle) == _normalize_handle(post_author_handle))
    if self_promotion or (same_author and _contains_sales_signal(normalized)):
        evidence = self_promotion or ["commenter_is_post_author_with_sales_signal"]
        return PrefilterDecision("set_aside", "commenter_advertising", tuple(evidence))

    return PrefilterDecision("eligible", "passed_prefilter", ())


def evaluate_with_jev(
    comment_text: str,
    *,
    post_context: Mapping[str, Any],
    knowledge_reader: InstagramKnowledgeReader | None,
    jev: JevCommentEvaluator | None,
) -> dict[str, Any]:
    """Retrieve knowledge, invoke Jev, and fail closed to a reviewable uncertain result."""
    category_id, product_id = _related_ids(post_context)
    try:
        if knowledge_reader is None:
            raise RuntimeError("knowledge_reader_unavailable")
        knowledge = knowledge_reader.retrieve_for_comment(
            comment_text=comment_text,
            category_id=category_id,
            product_id=product_id,
            post_context=post_context,
        )
    except Exception as exc:
        return _uncertain(
            post_context,
            reason=f"Knowledge retrieval failed ({type(exc).__name__}); human review is required.",
            evidence=_fallback_evidence(comment_text),
            knowledge_used={"source": {"kind": "unavailable"}, "answers": [], "indexed_information": []},
        )
    if not isinstance(knowledge, KnowledgeSnapshot):
        return _uncertain(
            post_context,
            reason="Knowledge retrieval returned an invalid snapshot; human review is required.",
            evidence=_fallback_evidence(comment_text),
            knowledge_used={"source": {"kind": "unavailable"}, "answers": [], "indexed_information": []},
        )

    knowledge_used = {
        "source": dict(knowledge.source),
        "answers": list(knowledge.answers),
        "indexed_information": list(knowledge.indexed_information),
    }
    if jev is None:
        return _uncertain(
            post_context,
            reason="Jev is not configured; human review is required.",
            evidence=_fallback_evidence(comment_text),
            knowledge_used=knowledge_used,
            knowledge=knowledge,
        )
    try:
        response = jev.evaluate_comment(
            comment_text=comment_text,
            knowledge=knowledge,
            post_context=post_context,
        )
    except Exception as exc:
        return _uncertain(
            post_context,
            reason=f"Jev evaluation failed ({type(exc).__name__}); human review is required.",
            evidence=_fallback_evidence(comment_text),
            knowledge_used=knowledge_used,
            knowledge=knowledge,
        )

    if not isinstance(response, Mapping):
        return _uncertain(
            post_context,
            reason="Jev returned an invalid response; human review is required.",
            evidence=_fallback_evidence(comment_text),
            knowledge_used=knowledge_used,
            knowledge=knowledge,
        )
    status = response.get("status")
    allowed = {"probably a buyer", "not a buyer", "uncertain"}
    evidence = _text_evidence(response.get("evidence"), comment_text)
    reason = str(response.get("reason") or "").strip()
    score = _score(response.get("score"))
    if (not isinstance(status, str) or status not in allowed or not reason
            or score is None or (status != "uncertain" and not evidence)):
        return _uncertain(
            post_context,
            reason="Jev returned an incomplete decision; human review is required.",
            evidence=evidence or _fallback_evidence(comment_text),
            knowledge_used=knowledge_used,
            knowledge=knowledge,
        )
    if not evidence:
        evidence = _fallback_evidence(comment_text)

    normalized_length = len(normalize_text(comment_text))
    threshold = jev_selection_threshold()
    if status != "uncertain" and normalized_length < MIN_COMMENT_DECISION_LENGTH:
        return _uncertain(
            post_context,
            reason=(f"Comment is too short for a definitive automated decision "
                    f"({normalized_length} normalized characters); human review is required."),
            evidence=evidence or _fallback_evidence(comment_text),
            knowledge_used=knowledge_used,
            knowledge=knowledge,
            score=score,
        )
    if status != "uncertain" and score < threshold:
        return _uncertain(
            post_context,
            reason=(f"Jev score {score:.2f} is below the selection threshold "
                    f"{threshold:.2f}; human review is required."),
            evidence=evidence or _fallback_evidence(comment_text),
            knowledge_used=knowledge_used,
            knowledge=knowledge,
            score=score,
        )

    intent = classify_comment(comment_text)
    if status != "uncertain" and intent.label == "other":
        return _uncertain(
            post_context,
            reason=("No clear purchase, price, or contact signal was found; "
                    "human review is required."),
            evidence=evidence or _fallback_evidence(comment_text),
            knowledge_used=knowledge_used,
            knowledge=knowledge,
            score=score,
        )

    related = response.get("related")
    if not isinstance(related, Mapping):
        related = _related_from_knowledge(knowledge)
    else:
        knowledge_related = _related_from_knowledge(knowledge)
        related = {
            "category": related.get("category") or knowledge_related["category"],
            "product": related.get("product") or knowledge_related["product"],
        }
    return {
        "status": status,
        "related": {"category": related.get("category"), "product": related.get("product")},
        "score": score,
        "reason": {"text": reason, "evidence": list(evidence)},
        "indexed_information_used": knowledge_used,
    }


def _uncertain(
    post_context: Mapping[str, Any],
    *,
    reason: str,
    evidence: Sequence[str],
    knowledge_used: Mapping[str, Any],
    knowledge: KnowledgeSnapshot | None = None,
    score: float = 0.0,
) -> dict[str, Any]:
    related = _related_from_knowledge(knowledge) if knowledge else _related_from_context(post_context)
    return {
        "status": "uncertain",
        "related": related,
        "score": score,
        "reason": {"text": reason, "evidence": list(evidence)},
        "indexed_information_used": dict(knowledge_used),
    }


def _related_ids(context: Mapping[str, Any]) -> tuple[str | None, str | None]:
    decision = context.get("category_decision")
    target = context.get("suggested_target")
    if not isinstance(decision, Mapping):
        decision = {}
    if not isinstance(target, Mapping):
        target = {}
    category_id = (decision.get("category_id") or decision.get("category_candidate_id")
                   or target.get("category_id"))
    product_id = (decision.get("product_id") or decision.get("product_candidate_id")
                  or target.get("product_id"))
    return _optional_id(category_id), _optional_id(product_id)


def _related_from_knowledge(knowledge: KnowledgeSnapshot) -> dict[str, Any]:
    return {
        "category": dict(knowledge.related_category) if knowledge.related_category else None,
        "product": dict(knowledge.related_product) if knowledge.related_product else None,
    }


def _related_from_context(context: Mapping[str, Any]) -> dict[str, Any]:
    decision = context.get("category_decision")
    if not isinstance(decision, Mapping):
        decision = {}
    target = context.get("suggested_target")
    if not isinstance(target, Mapping):
        target = {}
    return {
        "category": {"id": decision.get("category_id") or decision.get("category_candidate_id") or target.get("category_id"),
                     "name": decision.get("category") or decision.get("category_candidate")},
        "product": {"id": decision.get("product_id") or decision.get("product_candidate_id") or target.get("product_id"),
                    "name": decision.get("product") or decision.get("product_candidate")},
    }


def _optional_id(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _text_evidence(value: Any, comment_text: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    normalized_comment = normalize_text(comment_text)
    return tuple(dict.fromkeys(
        item.strip() for item in value
        if isinstance(item, str) and item.strip() and normalize_text(item.strip()) in normalized_comment
    ))


def _score(value: Any) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(score) or not 0.0 <= score <= 1.0:
        return None
    return score


def _fallback_evidence(comment_text: str) -> tuple[str, ...]:
    excerpt = comment_text.strip()[:500]
    return (excerpt,) if excerpt else ()


def _normalize_handle(value: str) -> str:
    return value.strip().lstrip("@").casefold()


def _contains_sales_signal(normalized: str) -> bool:
    return any(phrase in normalized for phrase in ("فروش", "موجود", "سفارش", "دایرکت"))
