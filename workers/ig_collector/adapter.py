"""
Instagram collection adapter and fetch interface (P08.09 / R39).

Decouples direct network provider/session calls from collector orchestration.
Maps timeouts, challenges, and network errors into standard typed results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, List, Optional

from .backends.base import (
    AuthFailed,
    Fatal,
    InstagramBackend,
    InstagramComment,
    InstagramPost,
    NotFound,
    PrivateProfile,
    RateLimited,
    Transient,
    UnsupportedCapability,
)

log = logging.getLogger("ig_collector.adapter")


@dataclass(frozen=True)
class FetchResult:
    """
    Standardized result of an Instagram collection fetch operation.
    """
    success: bool
    data: Any = None
    status: str = "ok"
    error_kind: Optional[str] = None
    error_message: str = ""
    cooldown_seconds: int = 0

    @property
    def is_rate_limited(self) -> bool:
        return self.status == "rate_limited" or self.error_kind == "rate_limit"


class InstagramCollectionAdapter:
    """
    Adapter wrapping an InstagramBackend with standardized error classification and execution seams.
    """

    def __init__(self, backend: InstagramBackend, with_retry_fn: Optional[Callable[[Callable[[], Any]], Any]] = None):
        self.backend = backend
        self._with_retry = with_retry_fn or (lambda fn: fn())

    @property
    def name(self) -> str:
        return self.backend.name

    @property
    def backend_name(self) -> str:
        return self.backend.name

    def require_capability(self, capability: str) -> None:
        self.backend.require(capability)

    def post_by_shortcode(self, shortcode: str) -> InstagramPost:
        self.require_capability("post_by_ref")
        return self._with_retry(lambda: self.backend.post_by_shortcode(shortcode))

    def user_posts(self, username: str, limit: int, since: Optional[datetime] = None) -> List[InstagramPost]:
        self.require_capability("user_posts")
        return self._with_retry(lambda: self.backend.user_posts(username, limit, since))[:limit]

    def hashtag_posts(self, tag: str, limit: int, since: Optional[datetime] = None) -> List[InstagramPost]:
        self.require_capability("hashtag_posts")
        return self._with_retry(lambda: self.backend.hashtag_posts(tag, limit, since))

    def post_comments(self, post: InstagramPost, limit: int) -> List[InstagramComment]:
        self.require_capability("post_comments")
        return self._with_retry(lambda: self.backend.post_comments(post, limit))[:limit]

    def fetch_post_by_shortcode(self, shortcode: str) -> FetchResult:
        try:
            post = self.post_by_shortcode(shortcode)
            return FetchResult(success=True, data=post, status="ok")
        except Exception as exc:
            return self._map_exception(exc)

    def fetch_user_posts(self, username: str, limit: int, since: Optional[datetime] = None) -> FetchResult:
        try:
            posts = self.user_posts(username, limit, since)
            return FetchResult(success=True, data=posts, status="ok")
        except Exception as exc:
            return self._map_exception(exc)

    def fetch_hashtag_posts(self, tag: str, limit: int, since: Optional[datetime] = None) -> FetchResult:
        try:
            posts = self.hashtag_posts(tag, limit, since)
            return FetchResult(success=True, data=posts, status="ok")
        except Exception as exc:
            return self._map_exception(exc)

    def fetch_post_comments(self, post: InstagramPost, limit: int) -> FetchResult:
        try:
            comments = self.post_comments(post, limit)
            return FetchResult(success=True, data=comments, status="ok")
        except Exception as exc:
            return self._map_exception(exc)

    def _map_exception(self, exc: Exception) -> FetchResult:
        error_kind = getattr(exc, "kind", "error")
        status = self.backend.outcome_for_error(exc)
        cooldown = self.backend.request_cooldown_seconds() or (86400 if isinstance(exc, RateLimited) else 0)

        log.debug("Mapped backend exception %s to status=%s, kind=%s", type(exc).__name__, status, error_kind)
        return FetchResult(
            success=False,
            data=None,
            status=status,
            error_kind=error_kind,
            error_message=str(exc),
            cooldown_seconds=cooldown,
        )


class FakeCollectionAdapter:
    """
    Fake collection adapter for test fixture execution without any network or credentials.
    """

    def __init__(
        self,
        posts: Optional[List[InstagramPost]] = None,
        comments: Optional[List[InstagramComment]] = None,
        fail_with: Optional[Exception] = None,
    ):
        self.posts = posts or []
        self.comments = comments or []
        self.fail_with = fail_with
        self.calls: List[str] = []

    @property
    def name(self) -> str:
        return "fake"

    @property
    def backend_name(self) -> str:
        return "fake"

    def require_capability(self, capability: str) -> None:
        pass

    def fetch_post_by_shortcode(self, shortcode: str) -> FetchResult:
        self.calls.append(f"post_by_shortcode:{shortcode}")
        if self.fail_with:
            return FetchResult(success=False, status="error", error_message=str(self.fail_with))
        post = next((p for p in self.posts if p.shortcode == shortcode), None)
        if not post:
            return FetchResult(success=False, status="not_found", error_kind="not_found")
        return FetchResult(success=True, data=post)

    def fetch_user_posts(self, username: str, limit: int, since: Optional[datetime] = None) -> FetchResult:
        self.calls.append(f"user_posts:{username}")
        if self.fail_with:
            return FetchResult(success=False, status="error", error_message=str(self.fail_with))
        matched = [p for p in self.posts if p.username.lower() == username.lower()][:limit]
        return FetchResult(success=True, data=matched)

    def fetch_hashtag_posts(self, tag: str, limit: int, since: Optional[datetime] = None) -> FetchResult:
        self.calls.append(f"hashtag_posts:{tag}")
        if self.fail_with:
            return FetchResult(success=False, status="error", error_message=str(self.fail_with))
        return FetchResult(success=True, data=self.posts[:limit])

    def fetch_post_comments(self, post: InstagramPost, limit: int) -> FetchResult:
        self.calls.append(f"post_comments:{post.shortcode}")
        if self.fail_with:
            return FetchResult(success=False, status="error", error_message=str(self.fail_with))
        return FetchResult(success=True, data=self.comments[:limit])

    def post_by_shortcode(self, shortcode: str) -> InstagramPost:
        res = self.fetch_post_by_shortcode(shortcode)
        if not res.success:
            if res.status == "not_found":
                raise NotFound("post not found")
            raise Fatal(res.error_message or "failed to get post")
        return res.data

    def user_posts(self, username: str, limit: int, since: Optional[datetime] = None) -> List[InstagramPost]:
        res = self.fetch_user_posts(username, limit, since)
        if not res.success:
            raise Fatal(res.error_message or "failed to get user posts")
        return res.data

    def hashtag_posts(self, tag: str, limit: int, since: Optional[datetime] = None) -> List[InstagramPost]:
        res = self.fetch_hashtag_posts(tag, limit, since)
        if not res.success:
            raise Fatal(res.error_message or "failed to get hashtag posts")
        return res.data

    def post_comments(self, post: InstagramPost, limit: int) -> List[InstagramComment]:
        res = self.fetch_post_comments(post, limit)
        if not res.success:
            raise Fatal(res.error_message or "failed to get comments")
        return res.data
