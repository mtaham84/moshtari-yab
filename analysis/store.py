"""SQLite-backed shared inbox (queue) and verdict store for the analysis agent.

Any crawler enqueues ``SocialMessage`` objects; the worker pulls pending ones,
analyses them and writes ``AgentVerdict`` rows. Statuses:

    pending -> processing -> done | error
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from analysis.schemas import AgentVerdict, ContextMessage, SocialMessage, StageCost, TriageResult
from analysis.text import content_fingerprint

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS inbox (
    uid           TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    channel_id    TEXT,
    fingerprint   TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    attempts      INTEGER NOT NULL DEFAULT 0,
    last_error    TEXT,
    triage_json   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status, created_at);
CREATE INDEX IF NOT EXISTS idx_inbox_fp ON inbox(fingerprint);

CREATE TABLE IF NOT EXISTS verdicts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    message_uid      TEXT NOT NULL,
    source           TEXT NOT NULL,
    product_id       INTEGER,
    reached_stage    TEXT NOT NULL,
    final_decision   TEXT NOT NULL,
    fit_score        INTEGER,
    total_tokens     INTEGER NOT NULL DEFAULT 0,
    cost_usd         REAL NOT NULL DEFAULT 0,
    cost_toman       REAL NOT NULL DEFAULT 0,
    payload_json     TEXT NOT NULL,
    run_id           INTEGER,
    synced           INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verdicts_uid ON verdicts(message_uid);
CREATE INDEX IF NOT EXISTS idx_verdicts_decision ON verdicts(final_decision);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    stats_json   TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnalysisStore:
    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            from analysis.config import get_settings

            db_path = get_settings().db_path
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn = sqlite3.connect(":memory:") if db_path == ":memory:" else None
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(inbox)")}
            if "triage_json" not in cols:  # upgrade a milestone-1 database
                conn.execute("ALTER TABLE inbox ADD COLUMN triage_json TEXT")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = self._memory_conn or sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if self._memory_conn is None:
                conn.close()

    # ------------------------------------------------------------------ inbox
    def enqueue(self, message: SocialMessage) -> bool:
        """Insert a message. Returns False if the uid or an identical copy from the
        same author is already queued (dedup = zero wasted LLM spend)."""
        author_key = message.author.id or message.author.handle
        fp = content_fingerprint(message.source, author_key, message.text)
        now = _now()
        with self._conn() as conn:
            if conn.execute("SELECT 1 FROM inbox WHERE uid = ?", (message.uid,)).fetchone():
                return False
            if author_key and conn.execute("SELECT 1 FROM inbox WHERE fingerprint = ?", (fp,)).fetchone():
                return False
            conn.execute(
                "INSERT INTO inbox (uid, source, channel_id, fingerprint, payload_json, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)",
                (message.uid, message.source, message.channel_id, fp, message.model_dump_json(), now, now),
            )
        return True

    def enqueue_many(self, messages: list[SocialMessage]) -> int:
        return sum(1 for m in messages if self.enqueue(m))

    def add_context(self, uid: str, item: ContextMessage) -> bool:
        """Attach a late-arriving context message (e.g. a reply) to a still-pending message."""
        with self._conn() as conn:
            row = conn.execute("SELECT payload_json, status FROM inbox WHERE uid = ?", (uid,)).fetchone()
            if not row or row["status"] != "pending":
                return False
            msg = SocialMessage.model_validate_json(row["payload_json"])
            if any(c.id == item.id and c.relation == item.relation for c in msg.context):
                return False
            msg.context.append(item)
            conn.execute("UPDATE inbox SET payload_json = ?, updated_at = ? WHERE uid = ?", (msg.model_dump_json(), _now(), uid))
        return True

    def claim_pending(self, limit: int, source: str | None = None) -> list[SocialMessage]:
        """Atomically move up to ``limit`` pending messages to 'processing'."""
        with self._conn() as conn:
            sql = "SELECT uid, payload_json FROM inbox WHERE status = 'pending'"
            params: list = []
            if source:
                sql += " AND source = ?"
                params.append(source)
            sql += " ORDER BY created_at ASC LIMIT ?"
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            uids = [r["uid"] for r in rows]
            if uids:
                conn.executemany(
                    "UPDATE inbox SET status = 'processing', attempts = attempts + 1, updated_at = ? WHERE uid = ?",
                    [(_now(), u) for u in uids],
                )
        return [SocialMessage.model_validate_json(r["payload_json"]) for r in rows]

    def mark_done(self, uids: list[str]) -> None:
        with self._conn() as conn:
            conn.executemany("UPDATE inbox SET status = 'done', last_error = NULL, updated_at = ? WHERE uid = ?", [(_now(), u) for u in uids])

    def release(self, uids: list[str], error: str | None, max_attempts: int) -> None:
        """Return messages to the queue after a transient failure, or mark them as error."""
        with self._conn() as conn:
            for uid in uids:
                conn.execute(
                    "UPDATE inbox SET status = CASE WHEN attempts >= ? THEN 'error' ELSE 'pending' END,"
                    " last_error = ?, updated_at = ? WHERE uid = ?",
                    (max_attempts, error, _now(), uid),
                )

    def release_without_attempt(self, uids: list[str]) -> None:
        """Return messages to the queue without counting an attempt (e.g. budget exhausted)."""
        with self._conn() as conn:
            conn.executemany(
                "UPDATE inbox SET status = 'pending', attempts = MAX(attempts - 1, 0), updated_at = ? WHERE uid = ?",
                [(_now(), u) for u in uids],
            )

    def set_triage(self, uid: str, matches: list[TriageResult], cost: StageCost | None) -> None:
        """Cache the triage result so a released message is not screened (and paid for) twice."""
        data = {"matches": [m.model_dump() for m in matches], "cost": cost.model_dump() if cost else None}
        with self._conn() as conn:
            conn.execute("UPDATE inbox SET triage_json = ? WHERE uid = ?", (json.dumps(data, ensure_ascii=False), uid))

    def get_triage(self, uids: list[str]) -> dict[str, tuple[list[TriageResult], StageCost | None]]:
        if not uids:
            return {}
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT uid, triage_json FROM inbox WHERE triage_json IS NOT NULL AND uid IN ({','.join('?' * len(uids))})", uids
            ).fetchall()
        out = {}
        for r in rows:
            data = json.loads(r["triage_json"])
            out[r["uid"]] = ([TriageResult(**m) for m in data["matches"]], StageCost(**data["cost"]) if data.get("cost") else None)
        return out

    def recover_stale(self) -> int:
        """Messages left in 'processing' by a crashed worker go back to 'pending'."""
        with self._conn() as conn:
            return conn.execute("UPDATE inbox SET status = 'pending', updated_at = ? WHERE status = 'processing'", (_now(),)).rowcount

    def reset_for_replay(self, source: str | None = None, channel_id: str | None = None, uids: list[str] | None = None) -> int:
        """Put already-processed messages back in the queue and delete their old verdicts."""
        where, params = ["1=1"], []
        if source:
            where.append("source = ?")
            params.append(source)
        if channel_id:
            where.append("channel_id = ?")
            params.append(channel_id)
        if uids:
            where.append(f"uid IN ({','.join('?' * len(uids))})")
            params.extend(uids)
        clause = " AND ".join(where)
        with self._conn() as conn:
            selected = [r["uid"] for r in conn.execute(f"SELECT uid FROM inbox WHERE {clause}", params)]
            if selected:
                marks = ",".join("?" * len(selected))
                conn.execute(f"DELETE FROM verdicts WHERE message_uid IN ({marks})", selected)
                conn.execute(
                    f"UPDATE inbox SET status = 'pending', attempts = 0, last_error = NULL, triage_json = NULL, updated_at = ? WHERE uid IN ({marks})",
                    [_now(), *selected],
                )
        return len(selected)

    def get_message(self, uid: str) -> SocialMessage | None:
        with self._conn() as conn:
            row = conn.execute("SELECT payload_json FROM inbox WHERE uid = ?", (uid,)).fetchone()
        return SocialMessage.model_validate_json(row["payload_json"]) if row else None

    def iter_messages(self, status: str | None = None) -> Iterator[tuple[SocialMessage, str]]:
        with self._conn() as conn:
            sql = "SELECT payload_json, status FROM inbox"
            params: list = []
            if status:
                sql += " WHERE status = ?"
                params.append(status)
            rows = conn.execute(sql + " ORDER BY created_at", params).fetchall()
        for r in rows:
            yield SocialMessage.model_validate_json(r["payload_json"]), r["status"]

    # --------------------------------------------------------------- verdicts
    def save_verdicts(self, verdicts: list[AgentVerdict], run_id: int | None = None) -> None:
        with self._conn() as conn:
            conn.executemany(
                "INSERT INTO verdicts (message_uid, source, product_id, reached_stage, final_decision, fit_score,"
                " total_tokens, cost_usd, cost_toman, payload_json, run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        v.message_uid, v.source, v.product_id, v.reached_stage, v.final_decision,
                        v.analysis.fit_score if v.analysis else None, v.total_tokens, v.total_cost_usd,
                        v.total_cost_toman, v.model_dump_json(), run_id, v.created_at.isoformat(),
                    )
                    for v in verdicts
                ],
            )

    def list_verdicts(self, decision: str | None = None, unsynced_only: bool = False, limit: int | None = None) -> list[tuple[int, AgentVerdict]]:
        sql, params = "SELECT id, payload_json FROM verdicts WHERE 1=1", []
        if decision:
            sql += " AND final_decision = ?"
            params.append(decision)
        if unsynced_only:
            sql += " AND synced = 0"
        sql += " ORDER BY id"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [(r["id"], AgentVerdict.model_validate_json(r["payload_json"])) for r in rows]

    def mark_verdicts_synced(self, ids: list[int]) -> None:
        with self._conn() as conn:
            conn.executemany("UPDATE verdicts SET synced = 1 WHERE id = ?", [(i,) for i in ids])

    # ------------------------------------------------------------------- runs
    def start_run(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("INSERT INTO runs (started_at) VALUES (?)", (_now(),)).lastrowid)

    def finish_run(self, run_id: int, status: str, stats: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, stats_json = ? WHERE id = ?",
                (_now(), status, json.dumps(stats, ensure_ascii=False), run_id),
            )

    # ------------------------------------------------------------------ stats
    def stats(self) -> dict:
        """Funnel and cost overview across everything analysed so far."""
        with self._conn() as conn:
            inbox = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM inbox GROUP BY status")}
            per_msg = conn.execute(
                """
                SELECT message_uid,
                       MAX(CASE reached_stage WHEN 'deep' THEN 3 WHEN 'triage' THEN 2 ELSE 1 END) AS stage_rank,
                       MAX(final_decision = 'respond') AS has_opportunity,
                       SUM(cost_toman) AS toman, SUM(cost_usd) AS usd, SUM(total_tokens) AS tokens
                FROM verdicts GROUP BY message_uid
                """
            ).fetchall()
            opportunities = conn.execute("SELECT COUNT(*) FROM verdicts WHERE final_decision = 'respond'").fetchone()[0]
        analysed = len(per_msg)
        total_toman = sum(r["toman"] or 0 for r in per_msg)
        total_usd = sum(r["usd"] or 0 for r in per_msg)
        total_tokens = sum(r["tokens"] or 0 for r in per_msg)
        return {
            "inbox": inbox,
            "messages_analysed": analysed,
            "funnel": {
                "entered": analysed,
                "passed_prefilter": sum(1 for r in per_msg if r["stage_rank"] >= 2),
                "reached_deep": sum(1 for r in per_msg if r["stage_rank"] >= 3),
                "messages_with_opportunity": sum(1 for r in per_msg if r["has_opportunity"]),
                "opportunities": opportunities,
            },
            "cost": {
                "total_tokens": total_tokens,
                "total_usd": round(total_usd, 6),
                "total_toman": round(total_toman, 2),
                "avg_toman_per_message": round(total_toman / analysed, 3) if analysed else 0,
                "toman_per_opportunity": round(total_toman / opportunities, 2) if opportunities else None,
            },
        }
