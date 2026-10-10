"""Bridge between the Django panel («جوامع آنلاین») and the crawler.

The panel table (``public.discovery_monitoredcommunity``) is the list of groups to watch:
the crawler polls it, joins new links, pauses inactive ones and writes back status and counters
(telegram_chat_id, sync_status, sync_error, members_count, messages_scanned_count, last_scanned_at).
Nothing needs a restart: add a community in the panel and it is picked up on the next poll.

Rows may belong to a seller (PRIVATE) or to nobody (GLOBAL, business_id NULL); the crawler does not care:
a group is joined once however many rows point to it. ``sync_error`` holds a code the panel translates:
INVALID_LINK, NO_ACCESS, NOT_A_GROUP, BANNED, LIMIT_REACHED, FLOOD_WAIT:<seconds>, UNKNOWN:<detail>.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

from telegram_crawler.config import settings


@dataclass
class Community:
    id: int
    link: str
    is_active: bool
    chat_id: int | None
    status: str = "PENDING"
    error: str = ""


# Errors that will not go away by retrying the same link: retried only after the seller re-activates the row.
PERMANENT_ERRORS = {"INVALID_LINK", "NO_ACCESS", "NOT_A_GROUP", "BANNED"}


def classify_join_error(exc: BaseException) -> tuple[str, int]:
    """Exception raised while joining → (panel error code, seconds to wait before retrying; 0 = see PERMANENT_ERRORS)."""
    from telethon import errors as tg

    from telegram_crawler.ratelimit import FloodWaitTooLong
    from telegram_crawler.telegram_client import JoinError

    if isinstance(exc, (FloodWaitTooLong, tg.FloodWaitError)):
        seconds = int(getattr(exc, "seconds", 0) or 0) or 60
        return f"FLOOD_WAIT:{seconds}", seconds
    if isinstance(exc, JoinError):
        return exc.code, 0
    if isinstance(exc, (tg.UsernameNotOccupiedError, tg.UsernameInvalidError, tg.ChannelInvalidError,
                        tg.ChatInvalidError, tg.PeerIdInvalidError)):
        return "INVALID_LINK", 0
    if isinstance(exc, (tg.InviteHashExpiredError, tg.InviteHashInvalidError, tg.ChannelPrivateError,
                        tg.InviteRequestSentError)):
        return "NO_ACCESS", 0
    if isinstance(exc, tg.UserBannedInChannelError):
        return "BANNED", 0
    if isinstance(exc, tg.ChannelsTooMuchError):
        return "LIMIT_REACHED", 3600
    text = str(exc) or exc.__class__.__name__
    if isinstance(exc, ValueError) and ("No user has" in text or "Cannot find any entity" in text):
        return "INVALID_LINK", 0     # what Telethon's get_entity raises for an unknown username
    return f"UNKNOWN:{text[:200]}", 600


def normalize_link(link: str) -> str:
    """Same group written differently (https://t.me/x, t.me/x/, @x, X) → one key."""
    s = (link or "").strip()
    s = re.sub(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/", "", s, flags=re.I).strip("/")
    s = s.lstrip("@")
    return s if s.startswith(("+", "joinchat/")) else s.lower()


class PanelCommunities:
    def __init__(self, conn: psycopg.Connection, table: str | None = None, crawler_schema: str | None = None):
        self.conn = conn
        self.table = table or settings.panel_table
        self.crawler_schema = crawler_schema or settings.db_schema
        for name in (self.table, self.crawler_schema):
            if not re.fullmatch(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?", name):
                raise ValueError(f"invalid identifier: {name!r}")

    @classmethod
    def connect(cls, dsn: str | None = None, **kw: Any) -> "PanelCommunities":
        return cls(psycopg.connect(dsn or settings.database_url, autocommit=True, row_factory=dict_row), **kw)

    def available(self) -> bool:
        """False until Django has migrated the panel table (the crawler keeps waiting)."""
        row = self.conn.execute("SELECT to_regclass(%s) AS t", (self.table,)).fetchone()
        return bool(row and (row["t"] if isinstance(row, dict) else row[0]))

    def communities(self) -> list[Community]:
        if not self.available():
            return []
        rows = self.conn.execute(
            f"SELECT id, handle_or_link, is_active, telegram_chat_id, sync_status, sync_error FROM {self.table} "
            "WHERE platform = 'telegram' ORDER BY id").fetchall()
        return [Community(r["id"], r["handle_or_link"], r["is_active"], r["telegram_chat_id"], r["sync_status"],
                          r["sync_error"] or "") for r in rows]

    def mark_joined(self, community_id: int, chat_id: int, title: str | None, members: int | None) -> None:
        """Without title/members (a second source for a group joined earlier) they are taken from the archive."""
        chats = f"{self.crawler_schema}.tg_chats"
        self.conn.execute(
            f"UPDATE {self.table} SET telegram_chat_id = %s, sync_status = 'ACTIVE', sync_error = '', "
            f"members_count = COALESCE(%s, (SELECT members_count FROM {chats} WHERE chat_id = %s), members_count), "
            "name = CASE WHEN name = '' OR name = handle_or_link "
            f"THEN COALESCE(%s, (SELECT title FROM {chats} WHERE chat_id = %s), name) ELSE name END "
            "WHERE id = %s", (chat_id, members, chat_id, title, chat_id, community_id))

    def mark_error(self, community_ids: int | list[int], error: str) -> None:
        ids = [community_ids] if isinstance(community_ids, int) else list(community_ids)
        self.conn.execute(f"UPDATE {self.table} SET sync_status = 'ERROR', sync_error = %s WHERE id = ANY(%s)",
                          (error[:500], ids))

    def mark_waiting(self, community_ids: list[int], note: str) -> None:
        """Still queued (PENDING) but with a reason shown to the seller, e.g. FLOOD_WAIT:<s> or LIMIT_REACHED."""
        if community_ids:
            self.conn.execute(f"UPDATE {self.table} SET sync_status = 'PENDING', sync_error = %s "
                              "WHERE id = ANY(%s) AND telegram_chat_id IS NULL", (note[:500], community_ids))

    def mark_paused(self, community_ids: list[int]) -> None:
        if community_ids:
            self.conn.execute(f"UPDATE {self.table} SET sync_status = 'PAUSED' "
                              "WHERE id = ANY(%s) AND sync_status = 'ACTIVE'", (community_ids,))

    def refresh_counters(self) -> None:
        """messages_scanned_count / last_scanned_at of every joined community, from the archive."""
        self.conn.execute(
            f"""UPDATE {self.table} c SET messages_scanned_count = s.n, last_scanned_at = s.last
                FROM (SELECT chat_id, count(*) AS n, max(archived_at) AS last
                      FROM {self.crawler_schema}.tg_messages WHERE NOT is_context GROUP BY chat_id) s
                WHERE c.telegram_chat_id = s.chat_id""")
        self.conn.execute(   # every source of the same group shows the group's title and member count
            f"""UPDATE {self.table} c SET members_count = COALESCE(t.members_count, c.members_count),
                name = CASE WHEN (c.name = '' OR c.name = c.handle_or_link) AND t.title IS NOT NULL THEN t.title ELSE c.name END
                FROM {self.crawler_schema}.tg_chats t WHERE c.telegram_chat_id = t.chat_id
                AND (c.members_count IS DISTINCT FROM COALESCE(t.members_count, c.members_count)
                     OR ((c.name = '' OR c.name = c.handle_or_link) AND t.title IS NOT NULL))""")
