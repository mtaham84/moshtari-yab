from __future__ import annotations

from need_engine.platforms.base import Platform
from need_engine.x_filters import cheap_x_filter

CHAT_ID_PREFIX = "x:"
PUBLIC_CHAT_ID = f"{CHAT_ID_PREFIX}public"
PRODUCT_CHAT_PREFIX = f"{CHAT_ID_PREFIX}p:"
INDEPENDENT_WINDOW_INTRO = "Independent public X posts from different authors; do not treat them as a conversation or use another author's post as context."
PREFILTER = cheap_x_filter


def chat_id(product_id=None) -> str:
    return f"{PRODUCT_CHAT_PREFIX}{product_id}" if product_id is not None else PUBLIC_CHAT_ID


def is_chat_id(value: str) -> bool:
    return value.startswith(CHAT_ID_PREFIX)


def is_product_chat(value: str) -> bool:
    return value.startswith(PRODUCT_CHAT_PREFIX)


def product_id_from_chat(value: str) -> str | None:
    return value[len(PRODUCT_CHAT_PREFIX):] if is_product_chat(value) else None


def min_match_score(cfg) -> float:
    return float(cfg.x_min_match_score)


def batch_limits(cfg) -> tuple[int, int]:
    return cfg.x_batch_size, cfg.x_batch_max_chars


def prefilter_mode(cfg) -> str:
    return cfg.x_prefilter


def filter_enabled(cfg) -> bool:
    return prefilter_mode(cfg) in {"shadow", "on"}


def enabled(cfg) -> bool:
    return cfg.x_enabled


def mode(cfg) -> str:
    return cfg.x_mode


def profile_url(username: str | None) -> str | None:
    return f"https://x.com/{username}" if username else None


def outreach_enabled(product) -> bool:
    return product.x_outreach_enabled


def platform_chat_id(product_id: str | None = None) -> str:
    return chat_id(product_id)


X = Platform(
    key="x",
    id_prefix="x_",
    independent_posts=True,
    chat_id=platform_chat_id,
    prefilter=PREFILTER,
    message_url=lambda message: message.url,
    min_match_score=min_match_score,
)

