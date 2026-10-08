"""Engine-private state in its own SQLite file (never the application database).

Holds: fetch cursor, pending (not yet analysed) messages, recent analysed messages for context,
open need cards + their query vectors, product cards + vectors, LLM response cache, cost ledger,
and fingerprints of already emitted opportunities.
"""
from __future__ import annotations

import io
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from need_engine.schemas import ChatMessage, NeedCard, Product, ProductCard

_SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS pending (chat_id TEXT, message_id INTEGER, payload TEXT, PRIMARY KEY (chat_id, message_id));
CREATE TABLE IF NOT EXISTS recent (chat_id TEXT, message_id INTEGER, payload TEXT, PRIMARY KEY (chat_id, message_id));
CREATE TABLE IF NOT EXISTS needs (
    need_id TEXT PRIMARY KEY, chat_id TEXT, author_id TEXT, status TEXT, updated_at REAL,
    payload TEXT, summary_vec BLOB, query_vecs BLOB);
CREATE INDEX IF NOT EXISTS idx_needs_person ON needs(chat_id, author_id);
CREATE INDEX IF NOT EXISTS idx_needs_status ON needs(status);
CREATE TABLE IF NOT EXISTS products (product_id TEXT PRIMARY KEY, hash TEXT, payload TEXT, card TEXT, vecs BLOB, vec_kinds TEXT);
CREATE TABLE IF NOT EXISTS llm_cache (k TEXT PRIMARY KEY, payload TEXT);
CREATE TABLE IF NOT EXISTS costs (ts REAL, stage TEXT, model TEXT, prompt_tokens INTEGER, completion_tokens INTEGER,
    cached INTEGER, usd REAL, toman REAL, ref TEXT);
CREATE TABLE IF NOT EXISTS emitted (opportunity_id TEXT PRIMARY KEY, fingerprint TEXT, ts REAL, payload TEXT);
CREATE TABLE IF NOT EXISTS quota (day TEXT, model TEXT, n INTEGER, PRIMARY KEY (day, model));
"""


def _pack(a: np.ndarray | None) -> bytes | None:
    if a is None:
        return None
    buf = io.BytesIO()
    np.save(buf, np.asarray(a, dtype=np.float32), allow_pickle=False)
    return buf.getvalue()


def _unpack(b: bytes | None) -> np.ndarray | None:
    return None if b is None else np.load(io.BytesIO(b), allow_pickle=False)


class Store:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(_SCHEMA)
            self.conn.commit()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    # ── kv ──────────────────────────────────────────────────────────────────
    def get(self, k: str, default: Any = None) -> Any:
        rows = self._all("SELECT v FROM kv WHERE k = ?", (k,))
        return json.loads(rows[0]["v"]) if rows else default

    def set(self, k: str, v: Any) -> None:
        self._exec("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, json.dumps(v)))

    # ── pending / recent messages ───────────────────────────────────────────
    def add_pending(self, msgs: list[ChatMessage]) -> int:
        with self.lock:
            cur = self.conn.executemany(
                "INSERT OR IGNORE INTO pending (chat_id, message_id, payload) VALUES (?, ?, ?)",
                [(m.chat_id, m.message_id, m.model_dump_json()) for m in msgs])
            self.conn.commit()
            return cur.rowcount

    def pending_chats(self) -> list[str]:
        return [r["chat_id"] for r in self._all("SELECT DISTINCT chat_id FROM pending")]

    def pending(self, chat_id: str) -> list[ChatMessage]:
        rows = self._all("SELECT payload FROM pending WHERE chat_id = ? ORDER BY message_id", (chat_id,))
        return [ChatMessage.model_validate_json(r["payload"]) for r in rows]

    def mark_analysed(self, chat_id: str, msgs: list[ChatMessage], keep_recent: int) -> None:
        with self.lock:
            self.conn.executemany("DELETE FROM pending WHERE chat_id = ? AND message_id = ?", [(chat_id, m.message_id) for m in msgs])
            self.conn.executemany("INSERT OR REPLACE INTO recent (chat_id, message_id, payload) VALUES (?, ?, ?)",
                                  [(chat_id, m.message_id, m.model_dump_json()) for m in msgs])
            self.conn.execute("""DELETE FROM recent WHERE chat_id = ? AND message_id NOT IN
                                 (SELECT message_id FROM recent WHERE chat_id = ? ORDER BY message_id DESC LIMIT ?)""",
                              (chat_id, chat_id, max(keep_recent, 1)))
            self.conn.commit()

    def recent(self, chat_id: str, before_id: int, limit: int) -> list[ChatMessage]:
        rows = self._all("SELECT payload FROM recent WHERE chat_id = ? AND message_id < ? ORDER BY message_id DESC LIMIT ?",
                         (chat_id, before_id, limit))
        return [ChatMessage.model_validate_json(r["payload"]) for r in reversed(rows)]

    def message(self, chat_id: str, message_id: int) -> ChatMessage | None:
        for table in ("pending", "recent"):
            rows = self._all(f"SELECT payload FROM {table} WHERE chat_id = ? AND message_id = ?", (chat_id, message_id))
            if rows:
                return ChatMessage.model_validate_json(rows[0]["payload"])
        return None

    # ── needs ───────────────────────────────────────────────────────────────
    def save_need(self, n: NeedCard, summary_vec: np.ndarray | None, query_vecs: np.ndarray | None) -> None:
        self._exec("""INSERT OR REPLACE INTO needs (need_id, chat_id, author_id, status, updated_at, payload, summary_vec, query_vecs)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                   (n.need_id, n.chat_id, n.author_id, n.status, n.updated_at.timestamp(), n.model_dump_json(),
                    _pack(summary_vec), _pack(query_vecs)))

    def update_need(self, n: NeedCard) -> None:
        """Updates payload/status only (vectors untouched)."""
        self._exec("UPDATE needs SET status = ?, updated_at = ?, payload = ? WHERE need_id = ?",
                   (n.status, n.updated_at.timestamp(), n.model_dump_json(), n.need_id))

    def query_vecs(self, need_id: str) -> np.ndarray | None:
        rows = self._all("SELECT query_vecs FROM needs WHERE need_id = ?", (need_id,))
        return _unpack(rows[0]["query_vecs"]) if rows else None

    def person_needs(self, chat_id: str, author_id: str) -> list[tuple[NeedCard, np.ndarray | None]]:
        rows = self._all("SELECT payload, summary_vec FROM needs WHERE chat_id = ? AND author_id = ? AND status IN ('open', 'not_opportunity')",
                         (chat_id, author_id))
        return [(NeedCard.model_validate_json(r["payload"]), _unpack(r["summary_vec"])) for r in rows]

    def open_needs(self) -> list[tuple[NeedCard, np.ndarray | None]]:
        rows = self._all("SELECT payload, query_vecs FROM needs WHERE status = 'open'")
        return [(NeedCard.model_validate_json(r["payload"]), _unpack(r["query_vecs"])) for r in rows]

    def need(self, need_id: str) -> NeedCard | None:
        rows = self._all("SELECT payload FROM needs WHERE need_id = ?", (need_id,))
        return NeedCard.model_validate_json(rows[0]["payload"]) if rows else None

    def expire_needs(self, older_than_ts: float) -> list[str]:
        rows = self._all("SELECT need_id, payload FROM needs WHERE status = 'open' AND updated_at < ?", (older_than_ts,))
        out = []
        for r in rows:
            n = NeedCard.model_validate_json(r["payload"])
            n.status = "expired"
            self._exec("UPDATE needs SET status = 'expired', payload = ? WHERE need_id = ?", (n.model_dump_json(), n.need_id))
            out.append(n.need_id)
        return out

    def next_need_id(self) -> str:
        with self.lock:
            k = int(self.get("need_seq", 0)) + 1
            self.set("need_seq", k)
            return f"need_{k:06d}"

    # ── products ────────────────────────────────────────────────────────────
    def product_hashes(self) -> dict[str, str]:
        return {r["product_id"]: r["hash"] for r in self._all("SELECT product_id, hash FROM products")}

    def save_product(self, p: Product, card: ProductCard, vecs: np.ndarray, kinds: list[str]) -> None:
        self._exec("INSERT OR REPLACE INTO products (product_id, hash, payload, card, vecs, vec_kinds) VALUES (?, ?, ?, ?, ?, ?)",
                   (p.product_id, p.content_hash(), p.model_dump_json(), card.model_dump_json(), _pack(vecs), json.dumps(kinds)))

    def delete_products(self, ids: Iterable[str]) -> None:
        with self.lock:
            self.conn.executemany("DELETE FROM products WHERE product_id = ?", [(i,) for i in ids])
            self.conn.commit()

    def products(self) -> list[tuple[Product, ProductCard, np.ndarray]]:
        rows = self._all("SELECT payload, card, vecs FROM products ORDER BY product_id")
        return [(Product.model_validate_json(r["payload"]), ProductCard.model_validate_json(r["card"]), _unpack(r["vecs"])) for r in rows]

    # ── llm cache / costs / quota ───────────────────────────────────────────
    def cache_get(self, k: str) -> dict | None:
        rows = self._all("SELECT payload FROM llm_cache WHERE k = ?", (k,))
        return json.loads(rows[0]["payload"]) if rows else None

    def cache_put(self, k: str, v: dict) -> None:
        self._exec("INSERT OR REPLACE INTO llm_cache (k, payload) VALUES (?, ?)", (k, json.dumps(v, ensure_ascii=False)))

    def add_cost(self, stage: str, model: str, pt: int, ct: int, cached: bool, usd: float, toman: float, ref: str = "") -> None:
        self._exec("INSERT INTO costs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (time.time(), stage, model, pt, ct, int(cached), usd, toman, ref))

    def cost_summary(self, since_ts: float = 0.0) -> list[dict]:
        rows = self._all("""SELECT stage, model, COUNT(*) calls, SUM(cached) cached, SUM(prompt_tokens) pt, SUM(completion_tokens) ct,
                            SUM(usd) usd, SUM(toman) toman FROM costs WHERE ts >= ? GROUP BY stage, model ORDER BY stage""", (since_ts,))
        return [dict(r) for r in rows]

    def quota_used(self, day: str, model: str) -> int:
        rows = self._all("SELECT n FROM quota WHERE day = ? AND model = ?", (day, model))
        return int(rows[0]["n"]) if rows else 0

    def quota_add(self, day: str, model: str, n: int = 1, set_to: int | None = None) -> None:
        if set_to is not None:
            self._exec("INSERT INTO quota VALUES (?, ?, ?) ON CONFLICT(day, model) DO UPDATE SET n = excluded.n", (day, model, set_to))
        else:
            self._exec("INSERT INTO quota VALUES (?, ?, ?) ON CONFLICT(day, model) DO UPDATE SET n = n + excluded.n", (day, model, n))

    # ── emitted opportunities ───────────────────────────────────────────────
    def emitted(self, opp_id: str) -> tuple[str, str] | None:
        """→ (fingerprint, payload json) of the last emitted version, if any."""
        rows = self._all("SELECT fingerprint, payload FROM emitted WHERE opportunity_id = ?", (opp_id,))
        return (rows[0]["fingerprint"], rows[0]["payload"]) if rows else None

    def mark_emitted(self, opp_id: str, fp: str, payload: str) -> None:
        self._exec("INSERT OR REPLACE INTO emitted VALUES (?, ?, ?, ?)", (opp_id, fp, time.time(), payload))
