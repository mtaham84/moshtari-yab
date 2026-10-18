from __future__ import annotations

from need_engine.platforms.base import Platform


def _message_url(message) -> str | None:
    if message.url:
        return message.url
    if message.chat_username:
        return f"https://t.me/{message.chat_username}/{message.message_id}"
    chat_id = message.chat_id[4:] if message.chat_id.startswith("-100") else message.chat_id.lstrip("-")
    return f"https://t.me/c/{chat_id}/{message.message_id}" if chat_id.isdigit() else None


TELEGRAM = Platform(
    key="telegram",
    id_prefix="telegram_",
    independent_posts=False,
    chat_id=lambda chat_id: str(chat_id),
    prefilter=None,
    message_url=_message_url,
)


def external_user_id(author_id: str) -> str:
    return f"{TELEGRAM.id_prefix}{author_id}"


def profile_url(username: str | None) -> str | None:
    return f"https://t.me/{username}" if username else None


