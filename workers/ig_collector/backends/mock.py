from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import BackendCapabilities, Fatal, InstagramBackend, InstagramComment, InstagramPost, NotFound


class MockInstagramBackend(InstagramBackend):
    name = "mock"
    capabilities = BackendCapabilities(user_posts=True, hashtag_posts=True, post_comments=True, post_by_ref=True)

    def __init__(self, fixture: str | Path | dict[str, Any] | None = None):
        if fixture is None:
            fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "instagram.json"
        if isinstance(fixture, dict):
            self.data = fixture
        else:
            path = Path(fixture)
            if not path.is_file():
                raise Fatal(f"mock fixture not found: {path}")
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise Fatal("mock fixture is unreadable or invalid JSON") from exc
        if not isinstance(self.data, dict):
            raise Fatal("mock fixture root must be a JSON object")

    @staticmethod
    def _posts(rows: Any, limit: int) -> list[InstagramPost]:
        output = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            shortcode = str(row.get("shortcode") or "").strip()
            output.append(InstagramPost(
                post_id=str(row.get("post_id") or shortcode), shortcode=shortcode,
                caption=str(row.get("caption") or ""), username=str(row.get("username") or ""),
                full_name=str(row.get("full_name") or ""), author_id=str(row["author_id"]) if row.get("author_id") else None,
                created_at=row.get("created_at"), like_count=_int(row.get("like_count")),
                comment_count=_int(row.get("comment_count")), media_type=str(row.get("media_type") or ""),
                hashtags=list(row.get("hashtags") or []), raw=row,
            ))
        return output[:max(0, limit)]

    def user_posts(self, username: str, limit: int, since: datetime | None) -> list[InstagramPost]:
        rows = (self.data.get("accounts") or {}).get(username, [])
        posts = self._posts(rows, len(rows) if isinstance(rows, list) else 0)
        return [post for post in posts if not since or not _date(post.created_at) or _date(post.created_at) > since][:limit]

    def hashtag_posts(self, tag: str, limit: int, since: datetime | None = None) -> list[InstagramPost]:
        normalized = tag.lstrip("#").casefold()
        rows = (self.data.get("hashtags") or {}).get(normalized, [])
        posts = self._posts(rows, len(rows) if isinstance(rows, list) else 0)
        if since:
            posts = [post for post in posts if _date(post.created_at) and _date(post.created_at) > since]
        return posts[:max(0, limit)]

    def post_by_shortcode(self, shortcode: str) -> InstagramPost:
        direct = (self.data.get("posts") or {}).get(shortcode)
        candidates = [direct] if isinstance(direct, dict) else []
        for group in (self.data.get("accounts") or {}, self.data.get("hashtags") or {}):
            if isinstance(group, dict):
                for rows in group.values():
                    candidates.extend(row for row in rows if isinstance(row, dict))
        for row in candidates:
            if str(row.get("shortcode") or "") == shortcode:
                return self._posts([row], 1)[0]
        raise NotFound("mock post was not found")

    def post_comments(self, post_ref: InstagramPost | str, limit: int) -> list[InstagramComment]:
        shortcode = post_ref.shortcode if isinstance(post_ref, InstagramPost) else _shortcode(post_ref)
        rows = (self.data.get("comments") or {}).get(shortcode, [])
        output = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            output.append(InstagramComment(
                comment_id=str(row.get("comment_id") or ""), text=str(row.get("text") or ""),
                username=str(row.get("username") or ""), full_name=str(row.get("full_name") or ""),
                author_id=str(row["author_id"]) if row.get("author_id") else None,
                created_at=row.get("created_at"), raw=row,
            ))
        return output[:max(0, limit)]


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            from datetime import timezone
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _shortcode(value: str) -> str:
    match = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", value)
    return match.group(1) if match else value
