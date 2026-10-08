"""Database operations for storing and retrieving discovered leads."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram_crawler.config import settings
from telegram_crawler.models import LeadContext, MessageSnippet

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
    """Initialize database tables from schema.sql and apply schema migrations."""
    with get_connection(db_path) as conn:
        with open(SCHEMA_FILE, encoding="utf-8") as f:
            conn.executescript(f.read())
        cur = conn.execute("PRAGMA table_info(leads);")
        columns = {row["name"] for row in cur.fetchall()}
        if "is_synced" not in columns:
            conn.execute("ALTER TABLE leads ADD COLUMN is_synced INTEGER DEFAULT 0;")
        if "synced_at" not in columns:
            conn.execute("ALTER TABLE leads ADD COLUMN synced_at TEXT;")


def save_lead(lead: LeadContext, is_synced: bool = False, db_path: str | None = None) -> bool:
    """Save a detected lead into SQLite. Returns True if inserted, False if already exists."""
    payload_json = lead.model_dump_json()
    with get_connection(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO leads (
                    lead_id, group_id, group_title, target_msg_id,
                    user_id, user_name, user_username,
                    target_text, target_date, payload_json, is_synced, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead.lead_id,
                    lead.group.group_id,
                    lead.group.title,
                    lead.target_message.message_id,
                    lead.user.user_id,
                    lead.user.display_name,
                    lead.user.username,
                    lead.target_message.text,
                    lead.target_message.date.isoformat(),
                    payload_json,
                    1 if is_synced else 0,
                    datetime.now(timezone.utc).isoformat() if is_synced else None,
                ),
            )
            return True
        except sqlite3.IntegrityError:
            # Lead already exists
            return False


def mark_lead_synced(lead_id: str, is_synced: bool = True, db_path: str | None = None) -> None:
    """Mark a lead as synced or unsynced to the Django backend."""
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE leads SET is_synced = ?, synced_at = datetime('now') WHERE lead_id = ?",
            (1 if is_synced else 0, lead_id),
        )


def get_unsynced_leads(limit: int = 50, db_path: str | None = None) -> list[LeadContext]:
    """Retrieve leads that have not been synced to the Django backend yet."""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "SELECT payload_json FROM leads WHERE is_synced = 0 ORDER BY id ASC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        return [LeadContext.model_validate_json(r["payload_json"]) for r in rows]


def get_lead(lead_id: str, db_path: str | None = None) -> LeadContext | None:
    """Fetch a single lead by lead_id."""
    with get_connection(db_path) as conn:
        cur = conn.execute("SELECT payload_json FROM leads WHERE lead_id = ?", (lead_id,))
        row = cur.fetchone()
        if not row:
            return None
        return LeadContext.model_validate_json(row["payload_json"])


def list_leads(
    group_id: int | None = None,
    limit: int = 50,
    db_path: str | None = None,
) -> list[LeadContext]:
    """List recent leads, optionally filtered by group_id."""
    with get_connection(db_path) as conn:
        if group_id is not None:
            cur = conn.execute(
                "SELECT payload_json FROM leads WHERE group_id = ? ORDER BY id DESC LIMIT ?",
                (group_id, limit),
            )
        else:
            cur = conn.execute(
                "SELECT payload_json FROM leads ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        rows = cur.fetchall()
        return [LeadContext.model_validate_json(r["payload_json"]) for r in rows]


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


# ============================================================================
# Raw message archive (all messages, used for local context + resume + dataset)
# ============================================================================
def save_message(
    group_id: int,
    snippet: "MessageSnippet",
    sender_username: str | None = None,
    sender_is_bot: bool = False,
    is_candidate: bool = False,
    db_path: str | None = None,
) -> bool:
    """Store a message once. Returns True if newly inserted."""
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO messages (
                group_id, msg_id, sender_id, sender_name, sender_username, sender_is_bot,
                text, date, reply_to_msg_id, is_candidate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                group_id, snippet.message_id, snippet.sender_id, snippet.sender_name, sender_username,
                1 if sender_is_bot else 0, snippet.text, snippet.date.isoformat(),
                snippet.reply_to_msg_id, 1 if is_candidate else 0,
            ),
        )
        return cur.rowcount > 0


def mark_message_enqueued(group_id: int, msg_id: int, db_path: str | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE messages SET enqueued = 1 WHERE group_id = ? AND msg_id = ?", (group_id, msg_id))


def is_message_enqueued(group_id: int, msg_id: int, db_path: str | None = None) -> bool:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT enqueued FROM messages WHERE group_id = ? AND msg_id = ?", (group_id, msg_id)).fetchone()
        return bool(row and row["enqueued"])


def _row_to_snippet(row: sqlite3.Row) -> "MessageSnippet":
    from telegram_crawler.models import MessageSnippet

    return MessageSnippet(
        message_id=row["msg_id"],
        sender_id=row["sender_id"],
        sender_name=row["sender_name"],
        text=row["text"],
        date=datetime.fromisoformat(row["date"]),
        reply_to_msg_id=row["reply_to_msg_id"],
    )


def get_local_message(group_id: int, msg_id: int, db_path: str | None = None) -> "MessageSnippet | None":
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM messages WHERE group_id = ? AND msg_id = ?", (group_id, msg_id)).fetchone()
        return _row_to_snippet(row) if row else None


def get_local_previous(group_id: int, before_msg_id: int, limit: int, db_path: str | None = None) -> list["MessageSnippet"]:
    """Up to ``limit`` stored messages right before ``before_msg_id`` (chronological, empty texts dropped).
    May return fewer than ``limit`` if older messages were never stored; callers then fall back to Telegram."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE group_id = ? AND msg_id < ? ORDER BY msg_id DESC LIMIT ?",
            (group_id, before_msg_id, limit),
        ).fetchall()
    return [_row_to_snippet(r) for r in reversed(rows) if (r["text"] or "").strip()]


def get_local_replies(group_id: int, msg_id: int, limit: int = 10, db_path: str | None = None) -> list["MessageSnippet"]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE group_id = ? AND reply_to_msg_id = ? ORDER BY msg_id ASC LIMIT ?",
            (group_id, msg_id, limit),
        ).fetchall()
    return [_row_to_snippet(r) for r in rows if (r["text"] or "").strip()]


def count_local_messages(group_id: int | None = None, db_path: str | None = None) -> int:
    with get_connection(db_path) as conn:
        if group_id is None:
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM messages WHERE group_id = ?", (group_id,)).fetchone()[0]


def mark_message_candidate(group_id: int, msg_id: int, db_path: str | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute("UPDATE messages SET is_candidate = 1 WHERE group_id = ? AND msg_id = ?", (group_id, msg_id))
