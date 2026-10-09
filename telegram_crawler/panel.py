"""Bridge between the Django panel («جوامع آنلاین») and the crawler.

The panel table (``public.discovery_monitoredcommunity``) is the list of groups to watch:
the crawler polls it, joins new links, pauses inactive ones and writes back status and counters
(telegram_chat_id, sync_status, sync_error, members_count, messages_scanned_count, last_scanned_at).
Nothing needs a restart: add a community in the panel and it is picked up on the next poll.
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
            f"SELECT id, handle_or_link, is_active, telegram_chat_id, sync_status FROM {self.table} "
            "WHERE platform = 'telegram' ORDER BY id").fetchall()
        return [Community(r["id"], r["handle_or_link"], r["is_active"], r["telegram_chat_id"], r["sync_status"])
                for r in rows]

    def mark_joined(self, community_id: int, chat_id: int, title: str | None, members: int | None) -> None:
        self.conn.execute(
            f"UPDATE {self.table} SET telegram_chat_id = %s, sync_status = 'ACTIVE', sync_error = '', "
            "members_count = COALESCE(%s, members_count), "
            "name = CASE WHEN name = '' OR name = handle_or_link THEN COALESCE(%s, name) ELSE name END "
            "WHERE id = %s", (chat_id, members, title, community_id))

    def mark_error(self, community_id: int, error: str) -> None:
        self.conn.execute(f"UPDATE {self.table} SET sync_status = 'ERROR', sync_error = %s WHERE id = %s",
                          (error[:500], community_id))

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
