"""PostgreSQL archive: users, chats and messages (schema ``TG_DB_SCHEMA``, default ``crawler``)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import psycopg
from psycopg.rows import dict_row

from telegram_crawler.config import settings

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"

USER_COLS = ("user_id", "username", "usernames", "first_name", "last_name", "phone", "lang_code", "is_bot", "is_premium",
             "is_verified", "is_scam", "is_fake", "is_deleted", "raw")
CHAT_COLS = ("chat_id", "type", "title", "username", "about", "members_count", "linked_chat_id", "is_forum",
             "is_verified", "is_scam", "raw")
MESSAGE_COLS = ("chat_id", "message_id", "sender_user_id", "sender_chat_id", "date", "edit_date", "text", "entities",
                "reply_to_msg_id", "reply_to_top_id", "quote_text", "fwd_from_id", "fwd_from_name", "fwd_date",
                "via_bot_id", "post_author", "grouped_id", "media_type", "media", "views", "forwards", "is_service",
                "service_action", "is_pinned", "is_context", "raw")
_JSONB = {"raw", "entities", "media"}
_BOOLS = {"is_bot", "is_premium", "is_verified", "is_scam", "is_fake", "is_deleted", "is_forum", "is_service", "is_pinned",
          "is_context"}


def _values(rec: dict[str, Any], cols: Iterable[str]) -> list[Any]:
    return [bool(rec.get(c)) if c in _BOOLS else rec.get(c) for c in cols]


def _placeholders(cols: Iterable[str]) -> str:
    return ", ".join("%s::jsonb" if c in _JSONB else "%s" for c in cols)


class Archive:
    """Thin synchronous data-access layer. One connection, autocommit; every write is idempotent."""

    def __init__(self, dsn: str | None = None, schema: str | None = None):
        self.schema = schema or settings.db_schema
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", self.schema):
            raise ValueError(f"invalid schema name: {self.schema!r}")
        self.conn = psycopg.connect(dsn or settings.database_url, autocommit=True, row_factory=dict_row)
        self.init()

    def close(self) -> None:
        self.conn.close()

    def init(self) -> None:
        self.conn.execute(SCHEMA_FILE.read_text(encoding="utf-8").replace("{schema}", self.schema))

    def _t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    # ── users ───────────────────────────────────────────────────────────────
    def add_user(self, rec: dict[str, Any]) -> bool:
        """Insert a user the first time we see them (snapshot, never overwritten).

        Only a placeholder row (created by :meth:`ensure_user_id` when the entity was unknown) is filled in later.
        Returns True if a row was inserted or a placeholder was completed."""
        update = ", ".join(f"{c} = EXCLUDED.{c}" for c in USER_COLS[1:])
        cur = self.conn.execute(
            f"INSERT INTO {self._t('tg_users')} AS t ({', '.join(USER_COLS)}) VALUES ({_placeholders(USER_COLS)}) "
            f"ON CONFLICT (user_id) DO UPDATE SET {update} "
            "WHERE t.raw IS NULL AND t.username IS NULL AND t.first_name IS NULL", _values(rec, USER_COLS))
        return cur.rowcount > 0

    def ensure_user_id(self, user_id: int) -> None:
        """Placeholder row when Telegram did not give us the sender entity (keeps the FK valid)."""
        self.conn.execute(f"INSERT INTO {self._t('tg_users')} (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))

    def users_without_profile(self, limit: int = 20) -> list[int]:
        rows = self.conn.execute(
            f"SELECT user_id FROM {self._t('tg_users')} WHERE profile_fetched_at IS NULL AND NOT is_deleted "
            "ORDER BY first_seen_at LIMIT %s", (limit,)).fetchall()
        return [r["user_id"] for r in rows]

    def set_profile(self, user_id: int, bio: str | None) -> None:
        self.conn.execute(f"UPDATE {self._t('tg_users')} SET bio = %s, profile_fetched_at = now() WHERE user_id = %s",
                          (bio, user_id))

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        return self.conn.execute(f"SELECT * FROM {self._t('tg_users')} WHERE user_id = %s", (user_id,)).fetchone()

    # ── chats ───────────────────────────────────────────────────────────────
    def upsert_chat(self, rec: dict[str, Any], *, monitored: bool = False, join_link: str | None = None) -> None:
        """Monitored groups are refreshed on every start; other chats (senders, forward sources) only fill empty fields."""
        cols = CHAT_COLS + ("is_monitored", "join_link")
        values = _values(rec, CHAT_COLS) + [monitored, join_link]
        if monitored:
            fields = ", ".join(f"{c} = COALESCE(EXCLUDED.{c}, t.{c})" for c in CHAT_COLS[1:])
            conflict = (f"DO UPDATE SET {fields}, is_monitored = TRUE, "
                        "join_link = COALESCE(EXCLUDED.join_link, t.join_link), updated_at = now()")
        else:
            fields = ", ".join(f"{c} = COALESCE(t.{c}, EXCLUDED.{c})" for c in CHAT_COLS[2:] if c not in {"is_forum", "is_verified", "is_scam"})
            conflict = f"DO UPDATE SET {fields}, updated_at = now() WHERE t.raw IS NULL"
        self.conn.execute(f"INSERT INTO {self._t('tg_chats')} AS t ({', '.join(cols)}) VALUES ({_placeholders(cols)}) "
                          f"ON CONFLICT (chat_id) {conflict}", values)

    def ensure_chat_id(self, chat_id: int, chat_type: str = "channel") -> None:
        self.conn.execute(f"INSERT INTO {self._t('tg_chats')} (chat_id, type) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                          (chat_id, chat_type))

    def get_chat(self, chat_id: int) -> dict[str, Any] | None:
        return self.conn.execute(f"SELECT * FROM {self._t('tg_chats')} WHERE chat_id = %s", (chat_id,)).fetchone()

    def save_progress(self, chat_id: int, last_msg_id: int) -> None:
        self.conn.execute(f"UPDATE {self._t('tg_chats')} SET last_scanned_msg_id = GREATEST(last_scanned_msg_id, %s), "
                          "last_scanned_at = now() WHERE chat_id = %s", (last_msg_id, chat_id))

    # ── messages ────────────────────────────────────────────────────────────
    def add_message(self, rec: dict[str, Any]) -> bool:
        """Insert once (chat_id, message_id). Returns True if new."""
        cur = self.conn.execute(
            f"INSERT INTO {self._t('tg_messages')} ({', '.join(MESSAGE_COLS)}) VALUES ({_placeholders(MESSAGE_COLS)}) "
            "ON CONFLICT (chat_id, message_id) DO NOTHING", _values(rec, MESSAGE_COLS))
        return cur.rowcount > 0

    def existing_message_ids(self, chat_id: int, ids: Iterable[int]) -> set[int]:
        ids = list(set(ids))
        if not ids:
            return set()
        rows = self.conn.execute(f"SELECT message_id FROM {self._t('tg_messages')} WHERE chat_id = %s AND message_id = ANY(%s)",
                                 (chat_id, ids)).fetchall()
        return {r["message_id"] for r in rows}

    def get_message(self, chat_id: int, message_id: int) -> dict[str, Any] | None:
        return self.conn.execute(f"SELECT * FROM {self._t('tg_messages')} WHERE chat_id = %s AND message_id = %s",
                                 (chat_id, message_id)).fetchone()

    def count_messages(self, chat_id: int | None = None) -> int:
        if chat_id is None:
            return self.conn.execute(f"SELECT COUNT(*) AS n FROM {self._t('tg_messages')}").fetchone()["n"]
        return self.conn.execute(f"SELECT COUNT(*) AS n FROM {self._t('tg_messages')} WHERE chat_id = %s",
                                 (chat_id,)).fetchone()["n"]
