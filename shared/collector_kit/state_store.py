from __future__ import annotations

import hashlib
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .seen import SeenStore as SQLiteStateStore


class StateStore(Protocol):
    def has_seen(self, record: dict[str, Any]) -> bool: ...
    def add(self, record: dict[str, Any], daily_cap: int) -> bool: ...
    def contains(self, kind: str, item_id: str) -> bool: ...
    def add_many(self, records: list[dict[str, Any]]) -> None: ...
    def count_today(self, day: date | None = None) -> int: ...
    def increment_today(self, amount: int, day: date | None = None) -> None: ...
    def cooldown_until(self, now: datetime | None = None) -> str | None: ...
    def start_cooldown(self, seconds: int = 900, now: datetime | None = None) -> str: ...
    def schedule(self, queries: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]: ...
    def record_query(self, query: str, fetched: int, new_posts: int, error: str | None = None,
                     started_at: datetime | None = None) -> None: ...
    def last_ok(self, query: str) -> datetime | None: ...
    def get_seed_progress(self, seed: str) -> datetime | None: ...
    def set_seed_progress(self, seed: str, latest: str | None) -> None: ...
    def comments_were_fetched(self, shortcode: str) -> bool: ...
    def mark_comments_fetched(self, shortcode: str) -> None: ...
    def query_status(self) -> list[dict[str, Any]]: ...
    def export_state(self) -> dict[str, list[tuple]]: ...
    def count_items(self, kind: str) -> int: ...
    def get_setting(self, key: str) -> str | None: ...
    def set_setting(self, key: str, value: str) -> None: ...
    def close(self) -> None: ...


def _dsn() -> str:
    from psycopg.conninfo import make_conninfo

    url = os.getenv("TG_DATABASE_URL") or os.getenv("DATABASE_URL")
    if url:
        return url
    values = {
        "host": os.getenv("POSTGRES_HOST", "127.0.0.1"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "dbname": os.getenv("POSTGRES_DB", "customer_yab"),
        "user": os.getenv("POSTGRES_USER", "postgres"),
        "password": os.getenv("POSTGRES_PASSWORD", ""),
    }
    return make_conninfo(**{key: value for key, value in values.items() if value})


class PostgresStateStore:
    def __init__(self, platform: str = "x", dsn: str | None = None, schema: str | None = None):
        self.platform = platform
        self.dsn = dsn or _dsn()
        self.schema = schema or os.getenv("TG_DB_SCHEMA", "crawler")
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", self.schema):
            raise ValueError("invalid collector state schema")
        self._init()

    def close(self) -> None:
        return None

    @staticmethod
    def _connect(dsn: str):
        import psycopg

        return psycopg.connect(dsn)

    def _init(self) -> None:
        with self._connect(self.dsn) as db:
            db.autocommit = True
            db.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.schema}.collector_seen_tweets (platform TEXT NOT NULL, tweet_key TEXT NOT NULL, first_seen TIMESTAMPTZ NOT NULL, PRIMARY KEY(platform,tweet_key))")
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.schema}.collector_seen_items (platform TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL, first_seen TIMESTAMPTZ NOT NULL, PRIMARY KEY(platform,kind,item_id))")
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.schema}.collector_daily_counts (platform TEXT NOT NULL, day DATE NOT NULL, collected INTEGER NOT NULL, PRIMARY KEY(platform,day))")
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.schema}.collector_settings (platform TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(platform,key))")
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.schema}.collector_query_runs (platform TEXT NOT NULL, query TEXT NOT NULL, kind TEXT NOT NULL, priority DOUBLE PRECISION NOT NULL DEFAULT 1, last_run_at TIMESTAMPTZ, runs INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0, new_posts INTEGER NOT NULL DEFAULT 0, last_error TEXT, last_ok_at TIMESTAMPTZ, PRIMARY KEY(platform,query))")
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.schema}.collector_seed_progress (platform TEXT NOT NULL, seed TEXT NOT NULL, last_seen_at TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(platform,seed))")
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.schema}.collector_comments_fetched (platform TEXT NOT NULL, post_shortcode TEXT NOT NULL, fetched_at TIMESTAMPTZ NOT NULL, PRIMARY KEY(platform,post_shortcode))")

    @staticmethod
    def _key(record: dict[str, Any]) -> str:
        value = "id:" + str(record["id"]) + (f"|p:{record['product_id']}" if record.get("product_id") else "") if record.get("id") else "text:" + str(record.get("text", ""))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def has_seen(self, record: dict[str, Any]) -> bool:
        with self._connect(self.dsn) as db:
            row = db.execute(f"SELECT 1 FROM {self.schema}.collector_seen_tweets WHERE platform=%s AND tweet_key=%s", (self.platform, self._key(record))).fetchone()
        return row is not None

    def add(self, record: dict[str, Any], daily_cap: int) -> bool:
        today = date.today()
        with self._connect(self.dsn) as db:
            with db.transaction():
                db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"collector:{self.platform}:{today}",))
                row = db.execute(f"SELECT collected FROM {self.schema}.collector_daily_counts WHERE platform=%s AND day=%s FOR UPDATE", (self.platform, today)).fetchone()
                if row and row[0] >= daily_cap:
                    return False
                if not row:
                    db.execute(f"INSERT INTO {self.schema}.collector_daily_counts(platform,day,collected) VALUES(%s,%s,0) ON CONFLICT DO NOTHING", (self.platform, today))
                    row = db.execute(f"SELECT collected FROM {self.schema}.collector_daily_counts WHERE platform=%s AND day=%s FOR UPDATE", (self.platform, today)).fetchone()
                    if row and row[0] >= daily_cap:
                        return False
                inserted = db.execute(
                    f"INSERT INTO {self.schema}.collector_seen_tweets(platform,tweet_key,first_seen) VALUES(%s,%s,now()) ON CONFLICT DO NOTHING RETURNING 1",
                    (self.platform, self._key(record)),
                ).fetchone()
                if inserted:
                    db.execute(f"INSERT INTO {self.schema}.collector_daily_counts(platform,day,collected) VALUES(%s,%s,1) ON CONFLICT(platform,day) DO UPDATE SET collected={self.schema}.collector_daily_counts.collected+1", (self.platform, today))
                return inserted is not None

    def contains(self, kind: str, item_id: str) -> bool:
        with self._connect(self.dsn) as db:
            row = db.execute(f"SELECT 1 FROM {self.schema}.collector_seen_items WHERE platform=%s AND kind=%s AND item_id=%s", (self.platform, kind, item_id)).fetchone()
        return row is not None

    def add_many(self, records: list[dict[str, Any]]) -> None:
        today = date.today()
        with self._connect(self.dsn) as db:
            with db.transaction():
                db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"collector:{self.platform}:{today}",))
                inserted = 0
                for row in records:
                    cursor = db.execute(f"INSERT INTO {self.schema}.collector_seen_items(platform,kind,item_id,first_seen) VALUES(%s,%s,%s,now()) ON CONFLICT DO NOTHING RETURNING 1", (self.platform, row["kind"], row["id"]))
                    inserted += int(cursor.fetchone() is not None)
                if inserted:
                    db.execute(f"INSERT INTO {self.schema}.collector_daily_counts(platform,day,collected) VALUES(%s,%s,%s) ON CONFLICT(platform,day) DO UPDATE SET collected={self.schema}.collector_daily_counts.collected+EXCLUDED.collected", (self.platform, today, inserted))

    def count_today(self, day: date | None = None) -> int:
        with self._connect(self.dsn) as db:
            row = db.execute(f"SELECT collected FROM {self.schema}.collector_daily_counts WHERE platform=%s AND day=%s", (self.platform, day or date.today())).fetchone()
        return int(row[0]) if row else 0

    def count_items(self, kind: str) -> int:
        with self._connect(self.dsn) as db:
            row = db.execute(f"SELECT COUNT(*) FROM {self.schema}.collector_seen_items WHERE platform=%s AND kind=%s", (self.platform, kind)).fetchone()
        return int(row[0])

    def increment_today(self, amount: int, day: date | None = None) -> None:
        with self._connect(self.dsn) as db:
            db.execute(f"INSERT INTO {self.schema}.collector_daily_counts(platform,day,collected) VALUES(%s,%s,%s) ON CONFLICT(platform,day) DO UPDATE SET collected={self.schema}.collector_daily_counts.collected+EXCLUDED.collected", (self.platform, day or date.today(), amount))

    def get_setting(self, key: str) -> str | None:
        with self._connect(self.dsn) as db:
            row = db.execute(f"SELECT value FROM {self.schema}.collector_settings WHERE platform=%s AND key=%s", (self.platform, key)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._connect(self.dsn) as db:
            db.execute(f"INSERT INTO {self.schema}.collector_settings(platform,key,value) VALUES(%s,%s,%s) ON CONFLICT(platform,key) DO UPDATE SET value=EXCLUDED.value", (self.platform, key, value))

    def cooldown_until(self, now: datetime | None = None) -> str | None:
        saved = self.get_setting("rate_limit_until")
        if not saved:
            return None
        try:
            until = datetime.fromisoformat(saved)
        except ValueError:
            return None
        current = now or datetime.now(timezone.utc)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return saved if until > current else None

    def start_cooldown(self, seconds: int = 900, now: datetime | None = None) -> str:
        value = ((now or datetime.now(timezone.utc)) + timedelta(seconds=seconds)).isoformat()
        self.set_setting("rate_limit_until", value)
        return value

    def schedule(self, queries: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        with self._connect(self.dsn) as db:
            with db.transaction():
                for item in queries:
                    db.execute(f"INSERT INTO {self.schema}.collector_query_runs(platform,query,kind,priority) VALUES(%s,%s,%s,%s) ON CONFLICT(platform,query) DO UPDATE SET kind=EXCLUDED.kind,priority=EXCLUDED.priority", (self.platform, item["query"], item.get("kind", "product"), item.get("priority", 1.0)))
                rows = db.execute(f"SELECT query,kind,priority,last_run_at FROM {self.schema}.collector_query_runs WHERE platform=%s AND query=ANY(%s)", (self.platform, [item["query"] for item in queries])).fetchall() if queries else []
        by_query = {row[0]: row for row in rows}
        def score(item):
            row = by_query[item["query"]]
            last, kind = row[3], row[1]
            stale = 1e12 if not last else max(1.0, (now - last).total_seconds())
            return -(stale * float(row[2]) * {"intent": 1.0, "problem": 0.8, "alternative": 0.7, "complaint": 0.6, "product": 0.4}.get(kind, 1.0)), item["query"]
        return sorted(queries, key=score)[:max(0, budget)]

    def record_query(self, query: str, fetched: int, new_posts: int, error: str | None = None,
                     started_at: datetime | None = None) -> None:
        with self._connect(self.dsn) as db:
                db.execute(f"UPDATE {self.schema}.collector_query_runs SET last_run_at=now(),runs=runs+1,fetched=fetched+%s,new_posts=new_posts+%s,last_error=%s,last_ok_at=COALESCE(%s,last_ok_at) WHERE platform=%s AND query=%s", (fetched, new_posts, error, started_at, self.platform, query))

    def last_ok(self, query: str) -> datetime | None:
        with self._connect(self.dsn) as db:
            row = db.execute(f"SELECT last_ok_at FROM {self.schema}.collector_query_runs WHERE platform=%s AND query=%s", (self.platform, query)).fetchone()
        return row[0] if row else None

    def query_status(self) -> list[dict[str, Any]]:
        with self._connect(self.dsn) as db:
            rows = db.execute(f"SELECT query,kind,last_run_at,fetched,new_posts FROM {self.schema}.collector_query_runs WHERE platform=%s ORDER BY query", (self.platform,)).fetchall()
        return [dict(zip(("query", "kind", "last_run_at", "fetched", "new_posts"), row)) for row in rows]

    def get_seed_progress(self, seed: str) -> datetime | None:
        with self._connect(self.dsn) as db:
            row = db.execute(f"SELECT last_seen_at FROM {self.schema}.collector_seed_progress WHERE platform=%s AND seed=%s", (self.platform, seed)).fetchone()
        return row[0] if row else None

    def set_seed_progress(self, seed: str, latest: str | None) -> None:
        with self._connect(self.dsn) as db:
            db.execute(f"INSERT INTO {self.schema}.collector_seed_progress(platform,seed,last_seen_at,updated_at) VALUES(%s,%s,%s,now()) ON CONFLICT(platform,seed) DO UPDATE SET last_seen_at=COALESCE(EXCLUDED.last_seen_at,{self.schema}.collector_seed_progress.last_seen_at),updated_at=now()", (self.platform, seed, latest))

    def comments_were_fetched(self, shortcode: str) -> bool:
        with self._connect(self.dsn) as db:
            row = db.execute(f"SELECT 1 FROM {self.schema}.collector_comments_fetched WHERE platform=%s AND post_shortcode=%s", (self.platform, shortcode)).fetchone()
        return row is not None

    def mark_comments_fetched(self, shortcode: str) -> None:
        with self._connect(self.dsn) as db:
            db.execute(f"INSERT INTO {self.schema}.collector_comments_fetched(platform,post_shortcode,fetched_at) VALUES(%s,%s,now()) ON CONFLICT DO NOTHING", (self.platform, shortcode))

    def import_missing_state(self, state: dict[str, list[tuple]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._connect(self.dsn) as db:
            with db.transaction():
                tables = {
                    "seen_tweets": ("collector_seen_tweets", "tweet_key,first_seen"),
                    "seen_items": ("collector_seen_items", "kind,item_id,first_seen"),
                    "daily_counts": ("collector_daily_counts", "day,collected"),
                    "collector_settings": ("collector_settings", "key,value"),
                    "query_runs": ("collector_query_runs", "query,kind,priority,last_run_at,runs,fetched,new_posts,last_error,last_ok_at"),
                    "seed_progress": ("collector_seed_progress", "seed,last_seen_at,updated_at"),
                    "comments_fetched": ("collector_comments_fetched", "post_shortcode,fetched_at"),
                }
                for name, (table, fields) in tables.items():
                    values = state[name]
                    if not values:
                        counts[name] = 0
                        continue
                    placeholders = ",".join(["%s"] * (len(values[0]) + 1))
                    statement = (f"INSERT INTO {self.schema}.{table}(platform,{fields}) VALUES ({placeholders}) "
                                 "ON CONFLICT DO NOTHING RETURNING 1")
                    inserted = 0
                    for row in values:
                        if name == "daily_counts":
                            exists = db.execute(
                                f"SELECT 1 FROM {self.schema}.collector_daily_counts WHERE platform=%s AND day=%s",
                                (self.platform, row[0]),
                            ).fetchone() is not None
                            if exists:
                                continue
                        inserted += int(db.execute(statement, (self.platform, *row)).fetchone() is not None)
                    counts[name] = inserted
        return counts

    def export_state(self) -> dict[str, list[tuple]]:
        with self._connect(self.dsn) as db:
            state: dict[str, list[tuple]] = {}
            tables = {"seen_tweets": "collector_seen_tweets", "seen_items": "collector_seen_items",
                      "daily_counts": "collector_daily_counts", "collector_settings": "collector_settings",
                      "query_runs": "collector_query_runs", "seed_progress": "collector_seed_progress",
                      "comments_fetched": "collector_comments_fetched"}
            columns = {"seen_tweets": "tweet_key,first_seen", "seen_items": "kind,item_id,first_seen",
                       "daily_counts": "day,collected", "collector_settings": "key,value",
                       "query_runs": "query,kind,priority,last_run_at,runs,fetched,new_posts,last_error,last_ok_at",
                       "seed_progress": "seed,last_seen_at,updated_at",
                       "comments_fetched": "post_shortcode,fetched_at"}
            for name, table in tables.items():
                rows = db.execute(
                    f"SELECT {columns[name]} FROM {self.schema}.{table} WHERE platform=%s", (self.platform,)
                ).fetchall()
                if name in {"query_runs", "seed_progress", "comments_fetched"}:
                    rows = [tuple(value.isoformat() if isinstance(value, datetime) else value for value in row)
                            for row in rows]
                state[name] = rows
            return state


def create_state_store(path: str | Path, platform: str, sqlite_factory=SQLiteStateStore) -> StateStore:
    backend = os.getenv("COLLECTOR_STATE_BACKEND", "sqlite").strip().lower()
    if backend == "sqlite":
        return sqlite_factory(path, platform=platform)
    if backend == "postgres":
        return PostgresStateStore(platform=platform)
    raise ValueError("COLLECTOR_STATE_BACKEND must be sqlite or postgres")
