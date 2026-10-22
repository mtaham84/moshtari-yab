from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class InstagramBackendError(RuntimeError):
    kind = "fatal"


class RateLimited(InstagramBackendError):
    kind = "rate_limit"


class AuthFailed(InstagramBackendError):
    kind = "auth"


class Transient(InstagramBackendError):
    kind = "transient"


class Fatal(InstagramBackendError):
    kind = "fatal"


class NotFound(Fatal):
    kind = "not_found"


class PrivateProfile(Fatal):
    kind = "private"


class UnsupportedCapability(InstagramBackendError):
    kind = "unsupported"


@dataclass(frozen=True)
class BackendCapabilities:
    user_posts: bool = False
    hashtag_posts: bool = False
    post_comments: bool = False
    post_by_ref: bool = False


@dataclass(frozen=True)
class InstagramPost:
    post_id: str
    shortcode: str
    caption: str
    username: str
    full_name: str = ""
    author_id: str | None = None
    created_at: Any = None
    like_count: int = 0
    comment_count: int = 0
    media_type: str = ""
    hashtags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/p/{self.shortcode}/" if self.shortcode else ""


@dataclass(frozen=True)
class InstagramComment:
    comment_id: str
    text: str
    username: str
    full_name: str = ""
    author_id: str | None = None
    created_at: Any = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


class InstagramBackend(ABC):
    """Read-only backend contract. Deliberately has no social write methods."""

    name = "base"
    capabilities = BackendCapabilities()

    def outcome_for_error(self, error: Exception) -> str:
        if isinstance(error, NotFound):
            return "not_found"
        if isinstance(error, PrivateProfile):
            return "private"
        return "rate_limited" if isinstance(error, RateLimited) else "error"

    def request_cooldown_seconds(self) -> int:
        return 0

    @abstractmethod
    def user_posts(self, username: str, limit: int, since: datetime | None) -> list[InstagramPost]:
        raise NotImplementedError

    @abstractmethod
    def hashtag_posts(self, tag: str, limit: int, since: datetime | None = None) -> list[InstagramPost]:
        raise NotImplementedError

    @abstractmethod
    def post_comments(self, post_ref: InstagramPost | str, limit: int) -> list[InstagramComment]:
        raise NotImplementedError

    def post_by_shortcode(self, shortcode: str) -> InstagramPost:
        """Resolve a shortcode where supported; older backends remain constructible."""
        raise UnsupportedCapability(f"backend {self.name} does not support post_by_ref")

    def require(self, capability: str) -> None:
        if not getattr(self.capabilities, capability, False):
            raise UnsupportedCapability(f"backend {self.name} does not support {capability}")
