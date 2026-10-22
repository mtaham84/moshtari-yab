"""Storage boundary for human labels on Instagram posts."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


PostLabelKey = tuple[str, str]
PostLabels = dict[PostLabelKey, dict[str, Any]]


@runtime_checkable
class PostLabelStore(Protocol):
    """Read/write interface for post labels and their pending review queue."""

    def load(self, *, business_id: str | int | None = None) -> PostLabels: ...

    def save(
        self,
        *,
        post_id: str,
        business_id: str | int,
        decision: str,
        category_id: str | int | None,
        product_id: str | int | None,
        operator_id: str | int,
        snapshot: Mapping[str, Any],
        audit: bool = False,
    ) -> dict[str, Any]: ...

    def pending(
        self,
        *,
        business_id: str | int,
        limit: int = 25,
        offset: int = 0,
        reason_code: str | None = None,
        category_id: str | int | None = None,
        post_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]: ...


class FilePostLabelStore:
    """File-backed adapter; consumers depend on :class:`PostLabelStore`."""

    def __init__(self, output_dir: str | Path | None = None) -> None:
        from .post_metrics import post_review_paths

        _, queue_path, labels_path = post_review_paths(output_dir)
        self.queue_path: Path = queue_path
        self.labels_path: Path = labels_path

    def load(self, *, business_id: str | int | None = None) -> PostLabels:
        from .post_metrics import load_post_labels

        return load_post_labels(self.labels_path, business_id=business_id)

    def save(
        self,
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
        from .post_metrics import save_post_label

        return save_post_label(
            self.labels_path, post_id=post_id, business_id=business_id,
            decision=decision, category_id=category_id, product_id=product_id,
            operator_id=operator_id, snapshot=snapshot, audit=audit,
        )

    def pending(
        self,
        *,
        business_id: str | int,
        limit: int = 25,
        offset: int = 0,
        reason_code: str | None = None,
        category_id: str | int | None = None,
        post_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        from .post_metrics import pending_post_reviews

        return pending_post_reviews(
            self.queue_path, self.labels_path, business_id=business_id,
            limit=limit, offset=offset, reason_code=reason_code,
            category_id=category_id, post_id=post_id,
        )


def create_post_label_store(output_dir: str | Path | None = None) -> PostLabelStore:
    """Application composition point for the active post-label backend."""
    return FilePostLabelStore(output_dir)
