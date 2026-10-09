"""Engine-private state in PostgreSQL (schema ``NE_STATE_SCHEMA``, default ``need_engine``), vectors in pgvector.

Holds: fetch cursor, pending (not yet analysed) messages, recent analysed messages for context,
need cards + their vectors, product cards + vectors, LLM response cache, cost ledger, emitted fingerprints,
and ``opportunities`` — the published Opportunity JSON the panel imports (``manage.py sync_opportunities``).
The engine writes only to this schema; crawler and Django tables are read through ``sources`` (read-only).
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from typing import Any, Iterable

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from need_engine.schemas import ChatMessage, NeedCard, Opportunity, Product, ProductCard

_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS {s};
CREATE TABLE IF NOT EXISTS {s}.kv (k TEXT PRIMARY KEY, v JSONB);
CREATE TABLE IF NOT EXISTS {s}.pending (chat_id TEXT, message_id BIGINT, payload JSONB NOT NULL, PRIMARY KEY (chat_id, message_id));
CREATE TABLE IF NOT EXISTS {s}.recent (chat_id TEXT, message_id BIGINT, payload JSONB NOT NULL, PRIMARY KEY (chat_id, message_id));
CREATE TABLE IF NOT EXISTS {s}.needs (
    need_id TEXT PRIMARY KEY, chat_id TEXT, author_id TEXT, status TEXT, updated_at DOUBLE PRECISION,
    payload JSONB NOT NULL, summary_vec vector);
CREATE INDEX IF NOT EXISTS needs_person_idx ON {s}.needs (chat_id, author_id);
CREATE INDEX IF NOT EXISTS needs_status_idx ON {s}.needs (status);
CREATE TABLE IF NOT EXISTS {s}.need_vectors (
    need_id TEXT REFERENCES {s}.needs (need_id) ON DELETE CASCADE, idx INTEGER, vec vector NOT NULL, PRIMARY KEY (need_id, idx));
CREATE TABLE IF NOT EXISTS {s}.products (product_id TEXT PRIMARY KEY, hash TEXT, payload JSONB NOT NULL, card JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS {s}.product_vectors (
    product_id TEXT REFERENCES {s}.products (product_id) ON DELETE CASCADE, idx INTEGER, kind TEXT, vec vector NOT NULL,
    PRIMARY KEY (product_id, idx));
CREATE TABLE IF NOT EXISTS {s}.llm_cache (k TEXT PRIMARY KEY, payload JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS {s}.costs (id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION, stage TEXT, model TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, cached BOOLEAN, usd DOUBLE PRECISION, toman DOUBLE PRECISION, ref TEXT);
CREATE TABLE IF NOT EXISTS {s}.emitted (opportunity_id TEXT PRIMARY KEY, fingerprint TEXT, ts DOUBLE PRECISION, payload JSONB);
CREATE TABLE IF NOT EXISTS {s}.quota (day TEXT, model TEXT, n INTEGER, PRIMARY KEY (day, model));
CREATE SEQUENCE IF NOT EXISTS {s}.opportunities_seq;
CREATE TABLE IF NOT EXISTS {s}.opportunities (
    opportunity_id TEXT PRIMARY KEY, seq BIGINT NOT NULL, status TEXT, payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS opportunities_seq_idx ON {s}.opportunities (seq);
"""


def _vec(a: Any) -> np.ndarray | None:
    if a is None:
        return None
    if hasattr(a, "to_numpy"):        # pgvector.Vector (pgvector-python >= 0.4)
        a = a.to_numpy()
    return np.asarray(a, dtype=np.float32)


class Store:
    def __init__(self, dsn: str, schema: str = "need_engine"):
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
            raise ValueError(f"invalid schema name: {schema!r}")
        self.dsn, self.schema = dsn, schema
        self.lock = threading.RLock()
        self._connect(create=True)

    def _connect(self, create: bool = False) -> None:
        self.conn = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)
        if create:
            self.conn.execute(_SCHEMA.replace("{s}", self.schema))
        register_vector(self.conn)

    def close(self) -> None:
        self.conn.close()

    def _t(self, name: str) -> str:
        return f"{self.schema}.{name}"

    def _run(self, fn):
        with self.lock:
            try:
                return fn(self.conn)
            except psycopg.OperationalError:     # database restarted → reconnect once
                self._connect()
                return fn(self.conn)

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> int:
        return self._run(lambda c: c.execute(sql.replace("{s}", self.schema), tuple(params)).rowcount)

    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return self._run(lambda c: c.execute(sql.replace("{s}", self.schema), tuple(params)).fetchall())

    # ── kv ──────────────────────────────────────────────────────────────────
    def get(self, k: str, default: Any = None) -> Any:
        rows = self._all("SELECT v FROM {s}.kv WHERE k = %s", (k,))
        return rows[0]["v"] if rows else default

    def set(self, k: str, v: Any) -> None:
        self._exec("INSERT INTO {s}.kv (k, v) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v", (k, Jsonb(v)))

    # ── pending / recent messages ───────────────────────────────────────────
    def add_pending(self, msgs: list[ChatMessage]) -> int:
        if not msgs:
            return 0
        return self._exec("""INSERT INTO {s}.pending (chat_id, message_id, payload)
                             SELECT * FROM unnest(%s::text[], %s::bigint[], %s::jsonb[]) ON CONFLICT DO NOTHING""",
                          ([m.chat_id for m in msgs], [m.message_id for m in msgs], [m.model_dump_json() for m in msgs]))

    def pending_chats(self) -> list[str]:
        return [r["chat_id"] for r in self._all("SELECT DISTINCT chat_id FROM {s}.pending ORDER BY chat_id")]

    def pending(self, chat_id: str) -> list[ChatMessage]:
        rows = self._all("SELECT payload FROM {s}.pending WHERE chat_id = %s ORDER BY message_id", (chat_id,))
        return [ChatMessage.model_validate(r["payload"]) for r in rows]

    def mark_analysed(self, chat_id: str, msgs: list[ChatMessage], keep_recent: int) -> None:
        ids = [m.message_id for m in msgs]

        def tx(c: psycopg.Connection) -> None:
            with c.transaction():
                c.execute(f"DELETE FROM {self._t('pending')} WHERE chat_id = %s AND message_id = ANY(%s)", (chat_id, ids))
                c.execute(f"""INSERT INTO {self._t('recent')} (chat_id, message_id, payload)
                              SELECT %s, * FROM unnest(%s::bigint[], %s::jsonb[])
                              ON CONFLICT (chat_id, message_id) DO UPDATE SET payload = EXCLUDED.payload""",
                          (chat_id, ids, [m.model_dump_json() for m in msgs]))
                c.execute(f"""DELETE FROM {self._t('recent')} WHERE chat_id = %s AND message_id NOT IN
                              (SELECT message_id FROM {self._t('recent')} WHERE chat_id = %s ORDER BY message_id DESC LIMIT %s)""",
                          (chat_id, chat_id, max(keep_recent, 1)))
                c.execute(f"""INSERT INTO {self._t('kv')} (k, v) VALUES ('messages_analysed', to_jsonb(%s::bigint))
                              ON CONFLICT (k) DO UPDATE SET v = to_jsonb(({self._t('kv')}.v #>> '{{}}')::bigint + %s)""",
                          (len(msgs), len(msgs)))

        self._run(tx)

    def recent(self, chat_id: str, before_id: int, limit: int) -> list[ChatMessage]:
        rows = self._all("SELECT payload FROM {s}.recent WHERE chat_id = %s AND message_id < %s ORDER BY message_id DESC LIMIT %s",
                         (chat_id, before_id, limit))
        return [ChatMessage.model_validate(r["payload"]) for r in reversed(rows)]

    def message(self, chat_id: str, message_id: int) -> ChatMessage | None:
        for table in ("pending", "recent"):
            rows = self._all(f"SELECT payload FROM {{s}}.{table} WHERE chat_id = %s AND message_id = %s", (chat_id, message_id))
            if rows:
                return ChatMessage.model_validate(rows[0]["payload"])
        return None

    # ── needs ───────────────────────────────────────────────────────────────
    def save_need(self, n: NeedCard, summary_vec: np.ndarray | None, query_vecs: np.ndarray | None) -> None:
        def tx(c: psycopg.Connection) -> None:
            with c.transaction():
                c.execute(f"""INSERT INTO {self._t('needs')} (need_id, chat_id, author_id, status, updated_at, payload, summary_vec)
                              VALUES (%s, %s, %s, %s, %s, %s, %s)
                              ON CONFLICT (need_id) DO UPDATE SET chat_id = EXCLUDED.chat_id, author_id = EXCLUDED.author_id,
                              status = EXCLUDED.status, updated_at = EXCLUDED.updated_at, payload = EXCLUDED.payload,
                              summary_vec = EXCLUDED.summary_vec""",
                          (n.need_id, n.chat_id, n.author_id, n.status, n.updated_at.timestamp(), Jsonb(n.model_dump(mode="json")),
                           _vec(summary_vec)))
                c.execute(f"DELETE FROM {self._t('need_vectors')} WHERE need_id = %s", (n.need_id,))
                if query_vecs is not None and len(query_vecs):
                    with c.cursor() as cur:
                        cur.executemany(f"INSERT INTO {self._t('need_vectors')} (need_id, idx, vec) VALUES (%s, %s, %s)",
                                        [(n.need_id, i, _vec(v)) for i, v in enumerate(query_vecs)])

        self._run(tx)

    def update_need(self, n: NeedCard) -> None:
        """Updates payload/status only (vectors untouched)."""
        self._exec("UPDATE {s}.needs SET status = %s, updated_at = %s, payload = %s WHERE need_id = %s",
                   (n.status, n.updated_at.timestamp(), Jsonb(n.model_dump(mode="json")), n.need_id))

    def query_vecs(self, need_id: str) -> np.ndarray | None:
        rows = self._all("SELECT vec FROM {s}.need_vectors WHERE need_id = %s ORDER BY idx", (need_id,))
        return np.stack([_vec(r["vec"]) for r in rows]) if rows else None

    def person_needs(self, chat_id: str, author_id: str) -> list[tuple[NeedCard, np.ndarray | None]]:
        rows = self._all("""SELECT payload, summary_vec FROM {s}.needs
                            WHERE chat_id = %s AND author_id = %s AND status IN ('open', 'not_opportunity') ORDER BY need_id""",
                         (chat_id, author_id))
        return [(NeedCard.model_validate(r["payload"]), _vec(r["summary_vec"])) for r in rows]

    def open_needs(self) -> list[tuple[NeedCard, np.ndarray | None]]:
        rows = self._all("SELECT need_id, payload FROM {s}.needs WHERE status = 'open' ORDER BY need_id")
        vecs: dict[str, list] = {}
        if rows:
            for r in self._all("""SELECT v.need_id, v.vec FROM {s}.need_vectors v JOIN {s}.needs n USING (need_id)
                                  WHERE n.status = 'open' ORDER BY v.need_id, v.idx"""):
                vecs.setdefault(r["need_id"], []).append(_vec(r["vec"]))
        return [(NeedCard.model_validate(r["payload"]), np.stack(vecs[r["need_id"]]) if r["need_id"] in vecs else None) for r in rows]

    def need(self, need_id: str) -> NeedCard | None:
        rows = self._all("SELECT payload FROM {s}.needs WHERE need_id = %s", (need_id,))
        return NeedCard.model_validate(rows[0]["payload"]) if rows else None

    def expire_needs(self, older_than_ts: float) -> list[str]:
        rows = self._all("SELECT payload FROM {s}.needs WHERE status = 'open' AND updated_at < %s", (older_than_ts,))
        out = []
        for r in rows:
            n = NeedCard.model_validate(r["payload"])
            n.status = "expired"
            self._exec("UPDATE {s}.needs SET status = 'expired', payload = %s WHERE need_id = %s", (Jsonb(n.model_dump(mode="json")), n.need_id))
            out.append(n.need_id)
        return out

    def next_need_id(self) -> str:
        with self.lock:
            k = int(self.get("need_seq", 0)) + 1
            self.set("need_seq", k)
            inst = self.get("instance_id")
            if not inst:  # unique per state → ids never collide after the state is reset
                inst = secrets.token_hex(3)
                self.set("instance_id", inst)
            return f"need_{k:06d}_{inst}"

    # ── products ────────────────────────────────────────────────────────────
    def product_hashes(self) -> dict[str, str]:
        return {r["product_id"]: r["hash"] for r in self._all("SELECT product_id, hash FROM {s}.products")}

    def save_product(self, p: Product, card: ProductCard, vecs: np.ndarray, kinds: list[str]) -> None:
        def tx(c: psycopg.Connection) -> None:
            with c.transaction():
                c.execute(f"""INSERT INTO {self._t('products')} (product_id, hash, payload, card) VALUES (%s, %s, %s, %s)
                              ON CONFLICT (product_id) DO UPDATE SET hash = EXCLUDED.hash, payload = EXCLUDED.payload, card = EXCLUDED.card""",
                          (p.product_id, p.content_hash(), Jsonb(p.model_dump(mode="json")), Jsonb(card.model_dump(mode="json"))))
                c.execute(f"DELETE FROM {self._t('product_vectors')} WHERE product_id = %s", (p.product_id,))
                if vecs is not None and len(vecs):
                    with c.cursor() as cur:
                        cur.executemany(f"INSERT INTO {self._t('product_vectors')} (product_id, idx, kind, vec) VALUES (%s, %s, %s, %s)",
                                        [(p.product_id, i, kinds[i] if i < len(kinds) else None, _vec(v)) for i, v in enumerate(vecs)])

        self._run(tx)

    def delete_products(self, ids: Iterable[str]) -> None:
        ids = list(ids)
        if ids:
            self._exec("DELETE FROM {s}.products WHERE product_id = ANY(%s)", (ids,))

    def products(self) -> list[tuple[Product, ProductCard, np.ndarray]]:
        rows = self._all("SELECT product_id, payload, card FROM {s}.products ORDER BY product_id")
        vecs: dict[str, list] = {}
        for r in self._all("SELECT product_id, vec FROM {s}.product_vectors ORDER BY product_id, idx"):
            vecs.setdefault(r["product_id"], []).append(_vec(r["vec"]))
        return [(Product.model_validate(r["payload"]), ProductCard.model_validate(r["card"]),
                 np.stack(vecs[r["product_id"]]) if r["product_id"] in vecs else None) for r in rows]

    # ── llm cache / costs / quota ───────────────────────────────────────────
    def cache_get(self, k: str) -> dict | None:
        rows = self._all("SELECT payload FROM {s}.llm_cache WHERE k = %s", (k,))
        return rows[0]["payload"] if rows else None

    def cache_put(self, k: str, v: dict) -> None:
        self._exec("INSERT INTO {s}.llm_cache (k, payload) VALUES (%s, %s) ON CONFLICT (k) DO UPDATE SET payload = EXCLUDED.payload",
                   (k, Jsonb(v)))

    def add_cost(self, stage: str, model: str, pt: int, ct: int, cached: bool, usd: float, toman: float, ref: str = "") -> None:
        self._exec("""INSERT INTO {s}.costs (ts, stage, model, prompt_tokens, completion_tokens, cached, usd, toman, ref)
                      VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", (time.time(), stage, model, pt, ct, bool(cached), usd, toman, ref))

    def cost_summary(self, since_ts: float = 0.0) -> list[dict]:
        rows = self._all("""SELECT stage, model, COUNT(*) AS calls, SUM(cached::int) AS cached, SUM(prompt_tokens) AS pt,
                            SUM(completion_tokens) AS ct, SUM(usd) AS usd, SUM(toman) AS toman
                            FROM {s}.costs WHERE ts >= %s GROUP BY stage, model ORDER BY stage, model""", (since_ts,))
        return [{k: (float(v) if k in ("usd", "toman") and v is not None else (int(v) if v is not None and k in ("calls", "cached", "pt", "ct") else v))
                 for k, v in r.items()} for r in rows]

    def totals(self) -> dict:
        """Lifetime numbers for the dashboard: messages reviewed, LLM calls and total cost."""
        r = self._all("SELECT COUNT(*) AS calls, COALESCE(SUM(toman), 0) AS toman, COALESCE(SUM(usd), 0) AS usd FROM {s}.costs")[0]
        n = int(self.get("messages_analysed", 0) or 0)
        return {"messages_analysed": n, "llm_calls": int(r["calls"]), "cost_toman": float(r["toman"]), "cost_usd": float(r["usd"]),
                "cost_per_message_toman": float(r["toman"]) / n if n else 0.0}

    def quota_used(self, day: str, model: str) -> int:
        rows = self._all("SELECT n FROM {s}.quota WHERE day = %s AND model = %s", (day, model))
        return int(rows[0]["n"]) if rows else 0

    def quota_add(self, day: str, model: str, n: int = 1, set_to: int | None = None) -> None:
        if set_to is not None:
            self._exec("INSERT INTO {s}.quota VALUES (%s, %s, %s) ON CONFLICT (day, model) DO UPDATE SET n = EXCLUDED.n", (day, model, set_to))
        else:
            self._exec("INSERT INTO {s}.quota VALUES (%s, %s, %s) ON CONFLICT (day, model) DO UPDATE SET n = {s}.quota.n + EXCLUDED.n",
                       (day, model, n))

    # ── emitted / published opportunities ───────────────────────────────────
    def emitted(self, opp_id: str) -> tuple[str, str] | None:
        """→ (fingerprint, payload json) of the last emitted version, if any."""
        rows = self._all("SELECT fingerprint, payload FROM {s}.emitted WHERE opportunity_id = %s", (opp_id,))
        return (rows[0]["fingerprint"], json.dumps(rows[0]["payload"], ensure_ascii=False)) if rows else None

    def mark_emitted(self, opp_id: str, fp: str, payload: str) -> None:
        self._exec("""INSERT INTO {s}.emitted VALUES (%s, %s, %s, %s::jsonb) ON CONFLICT (opportunity_id)
                      DO UPDATE SET fingerprint = EXCLUDED.fingerprint, ts = EXCLUDED.ts, payload = EXCLUDED.payload""",
                   (opp_id, fp, time.time(), payload))

    def publish(self, opp: Opportunity) -> None:
        """Latest version of an opportunity for the panel; ``seq`` grows on every change (sync cursor)."""
        self._exec("""INSERT INTO {s}.opportunities (opportunity_id, seq, status, payload)
                      VALUES (%s, nextval('{s}.opportunities_seq'), %s, %s::jsonb)
                      ON CONFLICT (opportunity_id) DO UPDATE SET seq = nextval('{s}.opportunities_seq'),
                      status = EXCLUDED.status, payload = EXCLUDED.payload, updated_at = now()""",
                   (opp.opportunity_id, opp.status, opp.model_dump_json()))

    def published_after(self, seq: int, limit: int = 500) -> list[dict]:
        return self._all("SELECT seq, payload FROM {s}.opportunities WHERE seq > %s ORDER BY seq LIMIT %s", (seq, limit))
