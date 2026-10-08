"""SQLite archive of monitored groups and their raw messages."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from telegram_crawler.config import settings
from telegram_crawler.models import MessageSnippet

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    target_path = Path(db_path or settings.db_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))


def update_group_monitor(
    group_id: int,
    title: str,
    link: str | None = None,
    last_msg_id: int = 0,
    db_path: str | None = None,
) -> None:
    """Update or register monitor state for a group."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO group_monitors (group_id, title, link, last_scanned_msg_id, last_scanned_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(group_id) DO UPDATE SET
                title = excluded.title,
                link = COALESCE(excluded.link, group_monitors.link),
                last_scanned_msg_id = MAX(group_monitors.last_scanned_msg_id, excluded.last_scanned_msg_id),
                last_scanned_at = datetime('now')
            """,
            (group_id, title, link, last_msg_id),
        )


def get_group_monitor(group_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    """Get monitor information for a group."""
    with get_connection(db_path) as conn:
        cur = conn.execute("SELECT * FROM group_monitors WHERE group_id = ?", (group_id,))
        row = cur.fetchone()
        if not row:
            return None
        return dict(row)


def save_message(
    group_id: int,
    snippet: MessageSnippet,
    sender_username: str | None = None,
    sender_is_bot: bool = False,
    db_path: str | None = None,
) -> bool:
    """Store a message once. Returns True if newly inserted."""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO messages (
                group_id, msg_id, sender_id, sender_name, sender_username, sender_is_bot,
                text, date, reply_to_msg_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_id, snippet.message_id, snippet.sender_id, snippet.sender_name, sender_username,
                1 if sender_is_bot else 0, snippet.text, snippet.date.isoformat(), snippet.reply_to_msg_id,
            ),
        )
        return cur.rowcount > 0


def get_local_message(group_id: int, msg_id: int, db_path: str | None = None) -> MessageSnippet | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM messages WHERE group_id = ? AND msg_id = ?", (group_id, msg_id)).fetchone()
    if not row:
        return None
    return MessageSnippet(
        message_id=row["msg_id"], sender_id=row["sender_id"], sender_name=row["sender_name"], text=row["text"],
        date=datetime.fromisoformat(row["date"]), reply_to_msg_id=row["reply_to_msg_id"],
    )


def count_local_messages(group_id: int | None = None, db_path: str | None = None) -> int:
    with get_connection(db_path) as conn:
        if group_id is None:
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM messages WHERE group_id = ?", (group_id,)).fetchone()[0]
