"""
Comment evaluation and filtering pipeline (P08.10 / R39).

Extracts record normalization, prefiltering, Jev qualification, and intent policy
evaluation from the monolith worker into a decoupled, testable component.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from instagram_discovery.comment_evaluation import (
    InstagramKnowledgeReader,
    JevCommentEvaluator,
    evaluate_with_jev,
    prefilter_comment,
)
from .backends.base import InstagramComment, InstagramPost
from .lexicon import PersianIntentLexicon
from .policies import COMMENT_POLICIES, buyer_request


@dataclass(frozen=True)
class CommentEvaluationOutcome:
    """
    Standardized result of evaluating an Instagram comment against prefilters,
    knowledge evaluation (Jev), and buyer intent policies.
    """
    accepted: bool
    record: Optional[Dict[str, Any]]
    evaluation_row: Dict[str, Any]
    is_filtered: bool
    is_uncertain: bool = False
    gated_by_jev: bool = False
    rejection_reason: Optional[str] = None
    keep_rejected_reason: Optional[str] = None


class CommentEvaluationPipeline:
    """
    Decoupled evaluation and filtering pipeline for Instagram comments.
    """

    def __init__(
        self,
        lexicon: PersianIntentLexicon,
        knowledge_reader: Optional[InstagramKnowledgeReader] = None,
        jev: Optional[JevCommentEvaluator] = None,
        min_intent_score: int = 1,
        policy_name: str = "seller_post",
    ):
        self.lexicon = lexicon
        self.knowledge_reader = knowledge_reader
        self.jev = jev
        self.min_intent_score = min_intent_score
        self.policy_name = policy_name

    def evaluate_comment(
        self,
        comment: InstagramComment,
        post: InstagramPost,
        post_record: Dict[str, Any],
        parent_post: Dict[str, Any],
        collection_source: Dict[str, Any],
        seed_key: str,
        backend_name: str,
        normalize_fn: Callable[..., Optional[Dict[str, Any]]],
        comment_index: int = 0,
    ) -> CommentEvaluationOutcome:
        """
        Runs a comment through normalization, prefiltering, Jev evaluation, and intent policy.
        """
        suggested = post_record.get("metadata", {}).get("suggested_target", {})
        business_id = suggested.get("business_id") if isinstance(suggested, dict) else None
        business_id_str = str(business_id) if business_id not in (None, "") else None

        # 1. Normalization
        record = normalize_fn(comment, post, seed_key, backend_name, self.lexicon)
        if record is None:
            raw_comment_id = str(comment.comment_id or f"unknown-{comment_index}").strip()
            eval_row = {
                "id": f"ig:{post.shortcode}:c{raw_comment_id}",
                "business_id": business_id_str,
                "post_id": f"ig:{post.shortcode}",
                "commenter_id": str(comment.author_id) if comment.author_id not in (None, "") else None,
                "commenter_username": str(comment.username or "").strip().lstrip("@") or None,
                "comment_text": str(comment.text or "").strip()[:2000],
                "collection_source": collection_source,
                "prefilter": {"status": "not_evaluable", "reason": "invalid_or_non_persian_or_missing_fields", "evidence": []},
                "jev": None,
            }
            return CommentEvaluationOutcome(
                accepted=False,
                record=None,
                evaluation_row=eval_row,
                is_filtered=True,
                rejection_reason="invalid_or_non_persian_or_missing_fields",
                keep_rejected_reason="invalid_or_non_persian_or_missing_fields",
            )

        # 2. Enrich metadata
        if parent_post.get("category_decision"):
            record["metadata"]["parent_post"] = parent_post
            record["metadata"]["category_decision"] = parent_post["category_decision"]
        else:
            record["metadata"]["parent_post"] = parent_post
        record["metadata"]["collection_source"] = collection_source

        # 3. Prefilter
        prefilter = prefilter_comment(
            record["text"],
            commenter_handle=record.get("author_handle"),
            post_author_handle=post_record.get("author_handle"),
        )
        prefilter_data = {
            "status": prefilter.status,
            "reason": prefilter.reason,
            "evidence": list(prefilter.evidence),
        }
        eval_row = {
            "id": record["id"],
            "business_id": business_id_str,
            "post_id": parent_post["id"],
            "commenter_id": record.get("author_id"),
            "commenter_username": str(record.get("author_handle") or "").lstrip("@") or None,
            "comment_text": record["text"][:2000],
            "collection_source": collection_source,
            "prefilter": prefilter_data,
            "jev": None,
        }
        record["metadata"]["jev_prefilter"] = prefilter_data

        if prefilter.status == "set_aside":
            return CommentEvaluationOutcome(
                accepted=False,
                record=record,
                evaluation_row=eval_row,
                is_filtered=True,
                rejection_reason="set_aside",
                keep_rejected_reason=None,
            )

        # 4. Jev evaluation
        category_context = record["metadata"].get("parent_post", {})
        decision = evaluate_with_jev(
            record["text"],
            post_context=category_context,
            knowledge_reader=self.knowledge_reader,
            jev=self.jev,
        )
        record["metadata"]["jev_evaluation"] = decision
        eval_row["jev"] = decision

        is_uncertain = (decision["status"] == "uncertain")
        if is_uncertain:
            record["metadata"]["review_queue_status"] = "queued"
            record["metadata"]["human_review_required"] = True
        else:
            record["metadata"]["review_queue_status"] = "not_queued"

        if self.jev is not None and decision["status"] != "probably a buyer":
            return CommentEvaluationOutcome(
                accepted=False,
                record=record,
                evaluation_row=eval_row,
                is_filtered=False,
                is_uncertain=is_uncertain,
                gated_by_jev=True,
                rejection_reason="not_probably_buyer",
                keep_rejected_reason=None,
            )

        # 5. Intent policy evaluation
        policy = COMMENT_POLICIES.get(self.policy_name, buyer_request)
        accepted_record = policy(record, post_record, self.min_intent_score)
        if accepted_record is None:
            reason = "not_seller_lead" if self.policy_name == "seller_post" else "intent_below_threshold"
            return CommentEvaluationOutcome(
                accepted=False,
                record=record,
                evaluation_row=eval_row,
                is_filtered=True,
                is_uncertain=is_uncertain,
                rejection_reason=reason,
                keep_rejected_reason=reason,
            )

        return CommentEvaluationOutcome(
            accepted=True,
            record=accepted_record,
            evaluation_row=eval_row,
            is_filtered=False,
            is_uncertain=is_uncertain,
            keep_rejected_reason=None,
        )

    @staticmethod
    def create_capped_evaluation_row(
        comment: InstagramComment,
        post: InstagramPost,
        post_record: Dict[str, Any],
        collection_source: Dict[str, Any],
        comment_index: int = 0,
    ) -> Dict[str, Any]:
        """
        Creates an evaluation record when collection is aborted due to daily cap.
        """
        raw_comment_id = str(comment.comment_id or f"unknown-{comment_index}").strip()
        suggested = post_record.get("metadata", {}).get("suggested_target", {})
        business_id = suggested.get("business_id") if isinstance(suggested, dict) else None
        business_id_str = str(business_id) if business_id not in (None, "") else None

        return {
            "id": f"ig:{post.shortcode}:c{raw_comment_id}",
            "business_id": business_id_str,
            "post_id": f"ig:{post.shortcode}",
            "commenter_id": str(comment.author_id) if comment.author_id not in (None, "") else None,
            "commenter_username": str(comment.username or "").strip().lstrip("@") or None,
            "comment_text": str(comment.text or "").strip()[:2000],
            "collection_source": collection_source,
            "prefilter": {"status": "not_evaluable", "reason": "daily_cap_reached", "evidence": []},
            "jev": None,
        }
