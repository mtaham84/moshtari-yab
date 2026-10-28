from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

from file_lock import exclusive_file_lock
from shared.records import RecordError, SourceRecord

log = logging.getLogger("x_ingest")


def _dsn() -> str:
    value = os.environ.get("NE_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if value:
        return value
    from psycopg.conninfo import make_conninfo

    params = {"host": os.environ.get("POSTGRES_HOST", "127.0.0.1"), "port": os.environ.get("POSTGRES_PORT", "5432"),
              "dbname": os.environ.get("POSTGRES_DB", "customer_yab"), "user": os.environ.get("POSTGRES_USER", "postgres"),
              "password": os.environ.get("POSTGRES_PASSWORD", "")}
    return make_conninfo(**{k: v for k, v in params.items() if v})


def validate_record(record: object) -> dict:
    if not isinstance(record, dict):
        raise ValueError("record must be a JSON object")
    schema_record = {**record, "kind": "post"} if record.get("kind") == "quote" else record
    parsed = SourceRecord.from_dict(schema_record)
    if parsed.source != "x":
        raise ValueError("source must equal x")
    tweet_id = parsed.id
    if not re.fullmatch(r"[0-9]+", tweet_id):
        raise ValueError("id must be a numeric X tweet id")
    if not isinstance(record.get("author_id"), str) or not record["author_id"].strip():
        raise ValueError("author_id must be a non-empty string")
    handle = str(parsed.author_handle or "").lstrip("@")
    if not handle:
        raise ValueError("author_handle is required")
    created = record.get("created_at")
    if created is not None:
        if not isinstance(created, str):
            raise ValueError("created_at must be ISO-8601 or null")
        try:
            parsed_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
            if parsed_dt < datetime.now(timezone.utc) - timedelta(days=10):
                raise ValueError(f"tweet created_at ({created}) is older than 10 days")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    metadata = parsed.meta
    kind = str(record.get("kind") or "post")
    if kind == "comment":
        raise ValueError("kind must be post, reply, or quote for X records")
    if kind not in {"post", "reply", "quote"}:
        raise ValueError("kind must be post, reply, or quote")
    thread_ids = {}
    for key in ("conversation_id", "in_reply_to_tweet_id"):
        raw_id = record.get(key)
        if raw_id is not None and not re.fullmatch(r"[0-9]+", str(raw_id)):
            raise ValueError(f"{key} must be a numeric tweet id")
        thread_ids[key] = int(raw_id) if raw_id is not None else None
    return {"tweet_id": int(tweet_id), "author_id": record["author_id"].strip(),
            "author_handle": handle, "author_name": record.get("author_name"), "text": record["text"],
            "created_at": created, "lang": metadata.get("lang"), "url": record.get("url"),
            "query": metadata.get("query"), "author_verified": bool(record.get("author_verified")),
            "author_bio": str(record.get("author_bio") or "")[:500], "kind": kind,
            **thread_ids, "in_reply_to_author_id": str(record.get("in_reply_to_author_id") or "") or None,
            "product_id": str(record["product_id"]) if record.get("product_id") else None, "raw": record}


def _load(path: str | Path, dsn: str | None = None) -> tuple[int, int]:
    import psycopg
    from psycopg.types.json import Jsonb

    schema = os.environ.get("NE_CRAWLER_SCHEMA", "crawler")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("NE_CRAWLER_SCHEMA must be a simple SQL schema name")
    count = 0
    invalid_count = 0
    with psycopg.connect(dsn or _dsn()) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {schema}.x_posts (
            row_id BIGSERIAL PRIMARY KEY, tweet_id BIGINT UNIQUE NOT NULL, author_id TEXT NOT NULL,
            author_handle TEXT NOT NULL, author_name TEXT, text TEXT NOT NULL, created_at TIMESTAMPTZ,
            lang TEXT, url TEXT, query TEXT, author_verified BOOLEAN NOT NULL DEFAULT FALSE, author_bio TEXT,
            raw JSONB NOT NULL, loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(), kind TEXT NOT NULL DEFAULT 'post',
            conversation_id BIGINT, in_reply_to_tweet_id BIGINT, in_reply_to_author_id TEXT)""")
        conn.execute(f"ALTER TABLE {schema}.x_posts ADD COLUMN IF NOT EXISTS author_verified BOOLEAN NOT NULL DEFAULT FALSE")
        conn.execute(f"ALTER TABLE {schema}.x_posts ADD COLUMN IF NOT EXISTS author_bio TEXT")
        conn.execute(f"ALTER TABLE {schema}.x_posts ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'post'")
        conn.execute(f"ALTER TABLE {schema}.x_posts ADD COLUMN IF NOT EXISTS conversation_id BIGINT")
        conn.execute(f"ALTER TABLE {schema}.x_posts ADD COLUMN IF NOT EXISTS in_reply_to_tweet_id BIGINT")
        conn.execute(f"ALTER TABLE {schema}.x_posts ADD COLUMN IF NOT EXISTS in_reply_to_author_id TEXT")
        # per-product mode: which seller product's search found the post (one row per post × product)
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {schema}.x_post_products (
            hit_id BIGSERIAL PRIMARY KEY, tweet_id BIGINT NOT NULL, product_id TEXT NOT NULL, query TEXT,
            found_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE (tweet_id, product_id))""")
        with Path(path).open(encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip() or line.lstrip().startswith("//"):
                    continue
                try:
                    record = validate_record(json.loads(line))
                except (json.JSONDecodeError, RecordError, ValueError) as exc:
                    invalid_count += 1
                    reason = exc.reason if isinstance(exc, RecordError) else str(exc)
                    log.warning("Skipping %s line %d: %s", path, line_no, reason)
                    continue
                conn.execute(f"""INSERT INTO {schema}.x_posts
                    (tweet_id, author_id, author_handle, author_name, text, created_at, lang, url, query, author_verified, author_bio, raw, kind, conversation_id, in_reply_to_tweet_id, in_reply_to_author_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tweet_id) DO UPDATE SET author_id=EXCLUDED.author_id, author_handle=EXCLUDED.author_handle,
                    author_name=EXCLUDED.author_name, text=EXCLUDED.text, created_at=EXCLUDED.created_at,
                    lang=EXCLUDED.lang, url=EXCLUDED.url, query=EXCLUDED.query, author_verified=EXCLUDED.author_verified,
                    author_bio=EXCLUDED.author_bio, raw=EXCLUDED.raw, kind=EXCLUDED.kind, conversation_id=EXCLUDED.conversation_id,
                    in_reply_to_tweet_id=EXCLUDED.in_reply_to_tweet_id, in_reply_to_author_id=EXCLUDED.in_reply_to_author_id""",
                    (record["tweet_id"], record["author_id"], record["author_handle"], record["author_name"],
                     record["text"], record["created_at"], record["lang"], record["url"], record["query"],
                     bool(record.get("author_verified")), record.get("author_bio"), Jsonb(record["raw"]), record["kind"],
                     record["conversation_id"], record["in_reply_to_tweet_id"], record["in_reply_to_author_id"]))
                if record["product_id"]:
                    conn.execute(f"""INSERT INTO {schema}.x_post_products (tweet_id, product_id, query) VALUES (%s,%s,%s)
                                     ON CONFLICT (tweet_id, product_id) DO NOTHING""",
                                 (record["tweet_id"], record["product_id"], record["query"]))
                try:
                    post_hash = hashlib.sha256(f"x_tweet:{record['tweet_id']}".encode("utf-8")).hexdigest()
                    conn.execute("""
                        INSERT INTO discovery_xcrawledpost (post_hash, tweet_id, author_handle, posted_at, crawled_at, tweet_url)
                        VALUES (%s, %s, %s, %s, now(), %s)
                        ON CONFLICT (post_hash) DO NOTHING
                    """, (post_hash, str(record["tweet_id"]), record["author_handle"], record["created_at"], record.get("url") or ""))
                except Exception as exc:
                    log.debug("Skipped discovery_xcrawledpost recording: %s", exc)
                count += 1
    if invalid_count:
        log.info("Skipped %s invalid X record(s) from %s", invalid_count, path)
    return count, invalid_count


def load(path: str | Path, dsn: str | None = None) -> int:
    """Upsert valid JSONL records and return the number processed."""
    count, _ = _load(path, dsn)
    return count


SEARCH_RUNS_DDL = """CREATE TABLE IF NOT EXISTS {schema}.x_search_runs (
    run_id BIGSERIAL PRIMARY KEY, ran_at TIMESTAMPTZ NOT NULL DEFAULT now(), product_id TEXT NOT NULL, query TEXT NOT NULL,
    fetched INTEGER NOT NULL DEFAULT 0, new_posts INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS x_search_runs_product_idx ON {schema}.x_search_runs (product_id, ran_at DESC)"""


def record_search_runs(searches: list[dict], dsn: str | None = None) -> int:
    """One row per (search, product): the seller-facing «what was searched and what came back» log."""
    import psycopg

    schema = os.environ.get("NE_CRAWLER_SCHEMA", "crawler")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("NE_CRAWLER_SCHEMA must be a simple SQL schema name")
    rows = [(s.get("ran_at") or datetime.now().astimezone().isoformat(), str(pid), str(s.get("query") or "")[:500],
             int(s.get("fetched") or 0), int((s.get("new_by_product") or {}).get(str(pid), 0)))
            for s in searches for pid in s.get("product_ids") or []]
    if not rows:
        return 0
    with psycopg.connect(dsn or _dsn()) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        for stmt in SEARCH_RUNS_DDL.format(schema=schema).split(";"):
            conn.execute(stmt)
        with conn.cursor() as c:
            c.executemany(f"INSERT INTO {schema}.x_search_runs (ran_at, product_id, query, fetched, new_posts) VALUES (%s,%s,%s,%s,%s)",
                          rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="x_ingest")
    parser.add_argument("--path", default="data/x_collected/latest.jsonl")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        source = Path(args.path).resolve()
        output_value = os.getenv("X_COLLECT_OUTPUT_DIR", "data/x_collected")
        output_root = Path(output_value)
        if not output_root.is_absolute():
            output_root = Path(__file__).resolve().parents[1] / output_root
        output_root = output_root.resolve()
        try:
            source.relative_to(output_root)
            within_output = True
        except ValueError:
            within_output = False
        lock = (exclusive_file_lock(output_root / ".retention_guard.lock")
                if within_output else nullcontext())
        with lock:
            count, invalid_count = _load(source)
    except Exception:
        log.exception("X JSONL import failed")
        return 1
    print(f"upserted {count} X posts and skipped {invalid_count} invalid lines from {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
