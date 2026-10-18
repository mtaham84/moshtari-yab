from __future__ import annotations

from need_engine.platforms.base import Platform
from need_engine.platforms.telegram import TELEGRAM
from need_engine.platforms.x import X, is_chat_id, is_product_chat as _is_product_chat

_PLATFORMS = {platform.key: platform for platform in (TELEGRAM, X)}


def get(key: str) -> Platform:
    return _PLATFORMS[key]


def for_message(message) -> Platform:
    return _PLATFORMS.get(message.platform, TELEGRAM)


def for_chat_id(chat_id: str) -> Platform:
    return X if is_chat_id(chat_id) else TELEGRAM


def is_product_chat(chat_id: str) -> bool:
    return _is_product_chat(chat_id)


def all_platforms() -> dict[str, Platform]:
    return dict(_PLATFORMS)
