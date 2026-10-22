from __future__ import annotations

import re
from typing import Any, Callable

from instagram_discovery.comment_intent import classify_comment

CommentPolicy = Callable[[dict[str, Any], dict[str, Any], int], dict[str, Any] | None]


def buyer_request(record: dict[str, Any], parent_record: dict[str, Any],
                  min_intent_score: int) -> dict[str, Any] | None:
    intent = record["metadata"]["intent"]
    if int(intent["score"]) >= min_intent_score:
        return record
    if (re.fullmatch(r"[0-9۰-۹٠-٩]+", record["text"].strip())
            and "request" in parent_record["metadata"]["intent"]["labels"]):
        record["metadata"]["intent"] = {"score": 1, "labels": ["request"]}
        return record
    return None


def seller_post(record: dict[str, Any], parent_record: dict[str, Any],
                min_intent_score: int) -> dict[str, Any] | None:
    intent = classify_comment(record["text"])
    if intent.label == "other":
        return None
    accepted = dict(record)
    accepted["metadata"] = dict(record["metadata"])
    parent_post = {
            "id": parent_record.get("metadata", {}).get("parent_source_id", parent_record["id"]),
            "caption": parent_record["text"][:160],
            "url": parent_record.get("url"),
        }
    post_metadata = parent_record.get("metadata", {})
    for provenance_key in ("suggested_target", "collection_source"):
        if provenance_key in post_metadata:
            parent_post[provenance_key] = post_metadata[provenance_key]
    if post_metadata.get("category_decision"):
        parent_post.update(post_metadata.get("parent_post", {}))
    accepted["metadata"].update({
        "parent_post": parent_post,
        "intent": {"label": intent.label, "cue": intent.cue},
    })
    return accepted


COMMENT_POLICIES: dict[str, CommentPolicy] = {
    "buyer_request": buyer_request,
    "seller_post": seller_post,
}
