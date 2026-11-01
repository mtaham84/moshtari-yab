"""
Tests for Instagram collection adapter seam (P08.09 / R39).
"""

from __future__ import annotations

from datetime import datetime, timezone
import pytest

from workers.ig_collector.adapter import (
    FetchResult,
    InstagramCollectionAdapter,
    FakeCollectionAdapter,
)
from workers.ig_collector.backends.base import (
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
from workers.ig_collector.worker import InstagramCollector


class MockBackend(InstagramBackend):
    """Controllable backend for adapter unit testing."""

    def __init__(self, name: str = "mock_backend", capabilities=None):
        self._name = name
        self.capabilities = set(capabilities or ["post_by_ref", "user_posts", "hashtag_posts", "post_comments"])
        self.calls = []
        self.raise_on_call = None

    @property
    def name(self) -> str:
        return self._name

    def require(self, capability: str) -> None:
        if capability not in self.capabilities:
            raise UnsupportedCapability(f"Backend does not support {capability}")

    def outcome_for_error(self, exc: Exception) -> str:
        if isinstance(exc, RateLimited):
            return "rate_limited"
        if isinstance(exc, PrivateProfile):
            return "private"
        if isinstance(exc, NotFound):
            return "not_found"
        return "error"

    def request_cooldown_seconds(self) -> int:
        return 900

    def post_by_shortcode(self, shortcode: str) -> InstagramPost:
        self.calls.append(("post_by_shortcode", shortcode))
        if self.raise_on_call:
            raise self.raise_on_call
        return InstagramPost(
            post_id=f"id_{shortcode}",
            shortcode=shortcode,
            username="seller_account",
            caption="سلام تست پست فروشگاهی",
            created_at=datetime.now(timezone.utc),
        )

    def user_posts(self, username: str, limit: int, since=None):
        self.calls.append(("user_posts", username, limit))
        if self.raise_on_call:
            raise self.raise_on_call
        return [
            InstagramPost(
                post_id=f"id_{i}",
                shortcode=f"code_{i}",
                username=username,
                caption=f"پست شماره {i}",
                created_at=datetime.now(timezone.utc),
            )
            for i in range(limit)
        ]

    def hashtag_posts(self, tag: str, limit: int, since=None):
        self.calls.append(("hashtag_posts", tag, limit))
        if self.raise_on_call:
            raise self.raise_on_call
        return [
            InstagramPost(
                post_id=f"tag_id_{i}",
                shortcode=f"tag_{i}",
                username="tag_user",
                caption=f"تگ تست {tag} {i}",
                created_at=datetime.now(timezone.utc),
            )
            for i in range(limit)
        ]

    def post_comments(self, post: InstagramPost, limit: int):
        self.calls.append(("post_comments", post.shortcode, limit))
        if self.raise_on_call:
            raise self.raise_on_call
        return [
            InstagramComment(
                comment_id=f"comm_{i}",
                username=f"buyer_{i}",
                text=f"قیمت این جنس چنده؟ {i}",
                created_at=datetime.now(timezone.utc),
            )
            for i in range(limit)
        ]


def test_adapter_successful_fetches():
    backend = MockBackend()
    adapter = InstagramCollectionAdapter(backend)

    res_post = adapter.fetch_post_by_shortcode("ABC123xyz")
    assert res_post.success is True
    assert res_post.status == "ok"
    assert res_post.data.shortcode == "ABC123xyz"

    res_user = adapter.fetch_user_posts("test_user", limit=2)
    assert res_user.success is True
    assert len(res_user.data) == 2

    res_tag = adapter.fetch_hashtag_posts("test_tag", limit=3)
    assert res_tag.success is True
    assert len(res_tag.data) == 3

    res_comm = adapter.fetch_post_comments(res_post.data, limit=2)
    assert res_comm.success is True
    assert len(res_comm.data) == 2


def test_adapter_maps_rate_limited_exception():
    backend = MockBackend()
    backend.raise_on_call = RateLimited("Too many requests from backend")
    adapter = InstagramCollectionAdapter(backend)

    res = adapter.fetch_post_by_shortcode("test_shortcode")
    assert res.success is False
    assert res.is_rate_limited is True
    assert res.status == "rate_limited"
    assert res.cooldown_seconds == 900
    assert "Too many requests" in res.error_message


def test_adapter_maps_not_found_and_private():
    backend = MockBackend()
    adapter = InstagramCollectionAdapter(backend)

    backend.raise_on_call = NotFound("User not found")
    res_not_found = adapter.fetch_user_posts("ghost", limit=1)
    assert res_not_found.success is False
    assert res_not_found.status == "not_found"

    backend.raise_on_call = PrivateProfile("Profile is private")
    res_private = adapter.fetch_user_posts("private_user", limit=1)
    assert res_private.success is False
    assert res_private.status == "private"


def test_adapter_direct_calls_with_retry():
    backend = MockBackend()
    attempt = 0

    def flaky_call(shortcode: str):
        nonlocal attempt
        attempt += 1
        if attempt < 3:
            raise Transient("temporary network glitch")
        return InstagramPost(
            post_id="id_retry",
            shortcode=shortcode,
            username="retry_seller",
            caption="توضیح پست بعد ریتری",
        )

    retries_run = 0

    def retry_wrapper(op):
        nonlocal retries_run
        for i in range(3):
            try:
                retries_run += 1
                return op()
            except Transient:
                if i == 2:
                    raise

    adapter = InstagramCollectionAdapter(backend, with_retry_fn=retry_wrapper)
    backend.post_by_shortcode = flaky_call

    post = adapter.post_by_shortcode("retry_code")
    assert post.shortcode == "retry_code"
    assert attempt == 3
    assert retries_run == 3


def test_fake_collection_adapter():
    mock_post = InstagramPost(
        post_id="id_fake123",
        shortcode="fake123",
        username="sample_user",
        caption="توضیح پست تستی فیک",
        created_at=datetime.now(timezone.utc),
    )
    mock_comm = InstagramComment(
        comment_id="c1",
        username="commenter",
        text="سلام موجود دارین؟",
        created_at=datetime.now(timezone.utc),
    )

    fake = FakeCollectionAdapter(posts=[mock_post], comments=[mock_comm])

    assert fake.name == "fake"
    assert fake.backend_name == "fake"

    p = fake.post_by_shortcode("fake123")
    assert p.shortcode == "fake123"

    posts = fake.user_posts("sample_user", limit=5)
    assert len(posts) == 1

    comments = fake.post_comments(mock_post, limit=5)
    assert len(comments) == 1
    assert comments[0].text == "سلام موجود دارین؟"

    # Not found handling
    res_missing = fake.fetch_post_by_shortcode("missing_code")
    assert res_missing.success is False
    assert res_missing.status == "not_found"


def test_collector_accepts_injected_adapter(tmp_path):
    backend = MockBackend()
    adapter = InstagramCollectionAdapter(backend)
    worker = InstagramCollector(
        backend=backend,
        output_dir=tmp_path / "out",
        state_path=tmp_path / "state.sqlite3",
        adapter=adapter,
    )
    assert worker.adapter is adapter
    assert worker.adapter.backend_name == "mock_backend"
