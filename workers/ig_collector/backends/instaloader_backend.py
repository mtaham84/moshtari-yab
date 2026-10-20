from __future__ import annotations

import os
import random
import re
import time
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import AuthFailed, BackendCapabilities, Fatal, InstagramBackend, InstagramComment, InstagramPost, RateLimited, Transient


class InstaloaderBackend(InstagramBackend):
    name = "instaloader"
    capabilities = BackendCapabilities(user_posts=True, hashtag_posts=True, post_comments=True)

    def __init__(self, *, loader: Any = None, module: Any = None, sleep_min: float | None = None,
                 sleep_max: float | None = None, sleeper=time.sleep, jitter=random.uniform):
        self._module = module
        self._loader = loader
        self._exceptions = getattr(module, "exceptions", None) if module is not None else None
        self.sleep_min = float(os.getenv("IG_COLLECT_SLEEP_MIN", "12") if sleep_min is None else sleep_min)
        self.sleep_max = float(os.getenv("IG_COLLECT_SLEEP_MAX", "25") if sleep_max is None else sleep_max)
        self._sleeper, self._jitter = sleeper, jitter

    def _ensure(self):
        if self._loader is not None:
            return self._module, self._loader
        try:
            import instaloader
            from instaloader import exceptions
        except ImportError as exc:
            raise Fatal("Instaloader backend requires optional package; run pip install instaloader.") from exc
        self._module = instaloader
        self._exceptions = exceptions
        self._loader = instaloader.Instaloader(
            sleep=True, quiet=True, download_pictures=False, download_videos=False,
            download_video_thumbnails=False, download_geotags=False, download_comments=False,
            save_metadata=False, compress_json=False,
        )
        session_path = os.getenv("IG_INSTALOADER_SESSION_FILE", "").strip()
        if session_path:
            try:
                session_file = Path(session_path).expanduser()
                username = session_file.name.removesuffix(".session")
                self._loader.load_session_from_file(username, filename=str(session_file))
            except Exception as exc:
                mapped = self._map_error(exc)
                if isinstance(mapped, AuthFailed):
                    raise mapped
                raise AuthFailed("Could not load the configured Instaloader session file.") from None
        return self._module, self._loader

    def _map_error(self, exc: Exception):
        if self._module is None:
            return Transient("Instaloader request failed")
        exceptions = getattr(self, "_exceptions", getattr(self._module, "exceptions", None))
        def matches(name):
            kind = getattr(exceptions, name, ()) if exceptions else ()
            return bool(kind) and isinstance(exc, kind)
        if matches("TooManyRequestsException"):
            return RateLimited("Instaloader rate limit received")
        if any(matches(name) for name in ("LoginRequiredException", "LoginException", "BadCredentialsException", "TwoFactorAuthRequiredException")):
            return AuthFailed("Instaloader authentication failed")
        if matches("ProfileNotExistsException") or matches("InvalidArgumentException"):
            return Fatal(f"Instaloader rejected the requested profile/argument ({type(exc).__name__})")
        if any(matches(name) for name in ("ConnectionException", "BadResponseException")):
            return Transient(f"Instaloader request failed ({type(exc).__name__})")
        return Fatal(f"Instaloader failed ({type(exc).__name__})")

    def _call(self, operation):
        try:
            return operation()
        except (RateLimited, AuthFailed, Transient, Fatal):
            raise
        except Exception as exc:
            raise self._map_error(exc) from None

    def _pause(self):
        self._sleeper(max(0.0, self._jitter(self.sleep_min, self.sleep_max)))

    def _iterate(self, source: Iterable[Any]) -> Iterator[Any]:
        try:
            yield from source
        except (RateLimited, AuthFailed, Transient, Fatal):
            raise
        except Exception as exc:
            raise self._map_error(exc) from None

    def user_posts(self, username: str, limit: int, since: datetime | None) -> list[InstagramPost]:
        if limit <= 0:
            return []
        module, loader = self._ensure()
        profile = self._call(lambda: module.Profile.from_username(loader.context, username))
        iterator = self._call(profile.get_posts)
        output = []
        for item in self._iterate(iterator):
            if since and _aware(item.date_utc) <= since:
                continue
            if output:
                self._pause()
            output.append(_post(item))
            if len(output) >= limit:
                break
        return output

    def hashtag_posts(self, tag: str, limit: int) -> list[InstagramPost]:
        if limit <= 0:
            return []
        module, loader = self._ensure()
        hashtag = self._call(lambda: module.Hashtag.from_name(loader.context, tag.lstrip("#")))
        iterator = self._call(hashtag.get_posts)
        output = []
        for item in self._iterate(iterator):
            if output:
                self._pause()
            output.append(_post(item))
            if len(output) >= limit:
                break
        return output

    def post_comments(self, post_ref: InstagramPost | str, limit: int) -> list[InstagramComment]:
        if limit <= 0:
            return []
        module, loader = self._ensure()
        shortcode = post_ref.shortcode if isinstance(post_ref, InstagramPost) else _shortcode(post_ref)
        post = self._call(lambda: module.Post.from_shortcode(loader.context, shortcode))
        output = []
        for item in self._iterate(self._call(post.get_comments)):
            if output:
                self._pause()
            owner = getattr(item, "owner", None)
            output.append(InstagramComment(
                comment_id=str(getattr(item, "id", "")), text=str(getattr(item, "text", "") or ""),
                username=str(getattr(owner, "username", "") or ""), full_name=str(getattr(owner, "full_name", "") or ""),
                author_id=str(getattr(owner, "userid", "") or "") or None,
                created_at=getattr(item, "created_at_utc", None) or getattr(item, "created_at", None),
                raw={"post_shortcode": shortcode},
            ))
            if len(output) >= limit:
                break
        return output


def _post(item: Any) -> InstagramPost:
    shortcode = str(getattr(item, "shortcode", "") or "")
    owner = getattr(item, "owner_profile", None)
    return InstagramPost(
        post_id=str(getattr(item, "mediaid", "") or shortcode), shortcode=shortcode,
        caption=str(getattr(item, "caption", "") or ""), username=str(getattr(item, "owner_username", "") or ""),
        full_name=str(getattr(owner, "full_name", "") or ""), author_id=str(getattr(item, "owner_id", "") or "") or None,
        created_at=getattr(item, "date_utc", None), like_count=int(getattr(item, "likes", 0) or 0),
        comment_count=int(getattr(item, "comments", 0) or 0),
        media_type="video" if bool(getattr(item, "is_video", False)) else "image",
        hashtags=list(getattr(item, "caption_hashtags", []) or []), raw={"shortcode": shortcode},
    )


def _aware(value: datetime) -> datetime:
    from datetime import timezone
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _shortcode(value: str) -> str:
    match = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", value)
    return match.group(1) if match else value
