from __future__ import annotations

import json
import os
import random
import socket
import time
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from .. import hikerapi_mapping as mapping
from .base import AuthFailed, BackendCapabilities, Fatal, InstagramBackend, InstagramComment, InstagramPost, NotFound, PrivateProfile, RateLimited, Transient


class HikerAPIBackend(InstagramBackend):
    name = "hikerapi"
    capabilities = BackendCapabilities(user_posts=True, hashtag_posts=True, post_comments=True)

    def request_cooldown_seconds(self) -> int:
        return 24 * 60 * 60

    def __init__(self, api_key: str | None = None, *, timeout: float = 20.0, opener=None,
                 proxy_url: str | None = None, sleep_min: float | None = None,
                 sleep_max: float | None = None, sleeper=time.sleep, jitter=random.uniform):
        self._api_key = api_key if api_key is not None else os.getenv("HIKERAPI_KEY", "")
        self.timeout = max(1.0, float(timeout))
        self._opener = opener
        self.proxy_url = proxy_url if proxy_url is not None else os.getenv("IG_PROXY_URL", "").strip()
        self.sleep_min = float(os.getenv("IG_COLLECT_SLEEP_MIN", "12") if sleep_min is None else sleep_min)
        self.sleep_max = float(os.getenv("IG_COLLECT_SLEEP_MAX", "25") if sleep_max is None else sleep_max)
        self._sleeper, self._jitter = sleeper, jitter
        self._has_requested = False

    def _request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._api_key:
            raise AuthFailed("HIKERAPI_KEY is not configured")
        if self._has_requested:
            self._sleeper(max(0.0, self._jitter(self.sleep_min, self.sleep_max)))
        query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
        url = f"{mapping.BASE_URL}{endpoint}" + (f"?{query}" if query else "")
        request = Request(url, headers={"x-access-key": self._api_key, "accept": "application/json"}, method="GET")
        opener = self._opener or build_opener(ProxyHandler({"http": self.proxy_url, "https": self.proxy_url}) if self.proxy_url else ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                status = int(getattr(response, "status", 200))
                body = response.read()
        except HTTPError as exc:
            self._raise_http(exc.code)
        except (TimeoutError, socket.timeout, URLError, OSError) as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise Transient("HikerAPI request timed out") from None
            raise Transient(f"HikerAPI network request failed ({type(exc).__name__})") from None
        self._has_requested = True
        self._raise_http(status)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise Fatal("HikerAPI returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise Fatal("HikerAPI response should be a JSON object")
        return payload

    @staticmethod
    def _raise_http(status: int) -> None:
        if 200 <= status < 300:
            return
        if status == 429:
            raise RateLimited("HikerAPI rate limit received (HTTP 429)")
        if status == 404:
            raise NotFound("HikerAPI profile or post was not found")
        if status == 403:
            raise PrivateProfile("HikerAPI profile is private or inaccessible")
        if status == 401:
            raise AuthFailed(f"HikerAPI authentication failed (HTTP {status})")
        if status in {408, 425} or status >= 500:
            raise Transient(f"HikerAPI temporary failure (HTTP {status})")
        raise Fatal(f"HikerAPI request rejected (HTTP {status})")

    def user_posts(self, username: str, limit: int, since: datetime | None) -> list[InstagramPost]:
        payload = self._request(mapping.USER_STREAM_ENDPOINT, {mapping.USER_STREAM_PARAMS["username"]: username})
        edges = _path(payload, mapping.USER_MEDIA_PATH, default=[])
        rows = [_path(edge, mapping.USER_MEDIA_NODE_PATH, default={}) for edge in edges if isinstance(edge, dict)]
        return [post for row in rows if (post := _post(row)) and (not since or not _is_before(post.created_at, since))][:limit]

    def hashtag_posts(self, tag: str, limit: int) -> list[InstagramPost]:
        tag = tag.lstrip("#")
        output = []
        cursor = None
        visited = set()
        while len(output) < limit:
            payload = self._request(mapping.HASHTAG_POSTS_ENDPOINT, {
                mapping.HASHTAG_PARAMS["tag"]: tag,
                mapping.HASHTAG_PARAMS["cursor"]: cursor,
            })
            for section in _path(payload, mapping.HASHTAG_MEDIA_PATH, default=[]):
                medias = _path(section, mapping.HASHTAG_MEDIA_CONTAINER_PATH, default=[])
                for row in medias:
                    media = _path(row, mapping.HASHTAG_MEDIA_NODE_PATH, default={})
                    post = _post(media)
                    if post:
                        output.append(post)
                        if len(output) >= limit:
                            break
                if len(output) >= limit:
                    break
            cursor = _path(payload, mapping.NEXT_PAGE_PATH)
            if not cursor or cursor in visited:
                break
            visited.add(cursor)
        return output[:limit]

    def post_comments(self, post_ref: InstagramPost | str, limit: int) -> list[InstagramComment]:
        post_id = post_ref.post_id if isinstance(post_ref, InstagramPost) else str(post_ref)
        if isinstance(post_ref, InstagramPost) and not post_id and post_ref.shortcode:
            raise Fatal("HikerAPI comment lookup requires a stable post id, not only a shortcode")
        output = []
        cursor = None
        visited = set()
        while len(output) < limit:
            payload = self._request(mapping.COMMENTS_ENDPOINT, {
                mapping.COMMENTS_PARAMS["post_id"]: post_id,
                mapping.COMMENTS_PARAMS["cursor"]: cursor,
            })
            rows = _path(payload, mapping.COMMENTS_PATH, default=[])
            if not isinstance(rows, list):
                raise Fatal("HikerAPI comments response has an unexpected shape")
            for row in rows:
                comment = _comment(row)
                if comment:
                    output.append(comment)
                    if len(output) >= limit:
                        break
            cursor = _path(payload, mapping.NEXT_PAGE_PATH)
            if not cursor or cursor in visited:
                break
            visited.add(cursor)
        return output[:limit]


def _path(value: Any, path: tuple, default=None):
    current = value
    try:
        for part in path:
            current = current[part]
        return current
    except (KeyError, IndexError, TypeError):
        return default


def _first(value: dict[str, Any], keys: tuple[str, ...], default=None):
    for key in keys:
        if value.get(key) not in (None, ""):
            return value[key]
    return default


def _first_path(value: dict[str, Any], paths: tuple[tuple, ...], default=None):
    for path in paths:
        result = _path(value, path)
        if result not in (None, ""):
            return result
    return default


def _post(raw: Any) -> InstagramPost | None:
    if not isinstance(raw, dict):
        return None
    shortcode = _first(raw, mapping.SHORTCODE_KEYS)
    media_id = _first(raw, mapping.MEDIA_ID_KEYS)
    caption = _first_path(raw, mapping.CAPTION_PATHS, "")
    author = _first_path(raw, mapping.AUTHOR_PATHS, "")
    if not shortcode:
        return None
    hashtags = _first(raw, mapping.HASHTAGS_KEYS, [])
    if not isinstance(hashtags, list):
        hashtags = []
    return InstagramPost(
        post_id=str(media_id or shortcode), shortcode=str(shortcode), caption=str(caption or ""),
        username=str(author or ""), author_id=_as_str(_first_path(raw, mapping.AUTHOR_ID_PATHS)),
        full_name=str(_first_path(raw, mapping.AUTHOR_NAME_PATHS, "") or ""),
        created_at=_first(raw, mapping.CREATED_AT_KEYS), like_count=_as_int(_first(raw, mapping.LIKE_COUNT_KEYS)),
        comment_count=_as_int(_first(raw, mapping.COMMENT_COUNT_KEYS)),
        media_type=str(_first(raw, mapping.MEDIA_TYPE_KEYS, "") or ""), hashtags=[str(tag) for tag in hashtags], raw=raw,
    )


def _comment(raw: Any) -> InstagramComment | None:
    if not isinstance(raw, dict):
        return None
    comment_id = _first(raw, mapping.COMMENT_ID_KEYS)
    text = _first_path(raw, mapping.COMMENT_TEXT_PATHS, "")
    if comment_id is None:
        return None
    return InstagramComment(
        comment_id=str(comment_id), text=str(text or ""),
        username=str(_first_path(raw, mapping.COMMENT_AUTHOR_PATHS, "") or ""),
        author_id=_as_str(_first_path(raw, mapping.COMMENT_AUTHOR_ID_PATHS)),
        full_name=str(_first_path(raw, mapping.COMMENT_AUTHOR_NAME_PATHS, "") or ""),
        created_at=_first(raw, mapping.COMMENT_CREATED_AT_KEYS), raw=raw,
    )


def _as_str(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_before(value: Any, since: datetime) -> bool:
    from ..worker import normalize_timestamp
    parsed = normalize_timestamp(value, warn=False)
    if parsed is None:
        return False
    return datetime.fromisoformat(parsed) <= since
