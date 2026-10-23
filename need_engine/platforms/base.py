from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Platform:
    key: str
    id_prefix: str
    independent_posts: bool
    chat_id: Callable[[Any], str]
    prefilter: Callable[..., Any] | None
    message_url: Callable[[Any], str | None]
    min_match_score: float | Callable[[Any], float] | None = None
