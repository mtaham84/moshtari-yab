from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

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
    tweet_id = str(record.get("id") or "")
    if not re.fullmatch(r"[0-9]+", tweet_id):
        raise ValueError("id must be a numeric X tweet id")
    if record.get("source") != "x":
        raise ValueError("source must equal x")
    if not isinstance(record.get("text"), str) or not record["text"].strip():
        raise ValueError("text must be a non-empty string")
    if not isinstance(record.get("author_id"), str) or not record["author_id"].strip():
        raise ValueError("author_id must be a non-empty string")
    handle = str(record.get("author_handle") or "").lstrip("@")
    if not handle:
        raise ValueError("author_handle is required")
    created = record.get("created_at")
    if created is not None:
        if not isinstance(created, str):
            raise ValueError("created_at must be ISO-8601 or null")
        try:
            datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("created_at must be ISO-8601 or null") from exc
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be an object")
    return {"tweet_id": int(tweet_id), "author_id": record["author_id"].strip(),
            "author_handle": handle, "author_name": record.get("author_name"), "text": record["text"],
            "created_at": created, "lang": metadata.get("lang"), "url": record.get("url"),
            "query": metadata.get("query"), "raw": record}


def load(path: str | Path, dsn: str | None = None) -> int:
    import psycopg
    from psycopg.types.json import Jsonb

    schema = os.environ.get("NE_CRAWLER_SCHEMA", "crawler")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("NE_CRAWLER_SCHEMA must be a simple SQL schema name")
    count = 0
    with psycopg.connect(dsn or _dsn()) as conn:
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        conn.execute(f"""CREATE TABLE IF NOT EXISTS {schema}.x_posts (
            row_id BIGSERIAL PRIMARY KEY, tweet_id BIGINT UNIQUE NOT NULL, author_id TEXT NOT NULL,
            author_handle TEXT NOT NULL, author_name TEXT, text TEXT NOT NULL, created_at TIMESTAMPTZ,
            lang TEXT, url TEXT, query TEXT, raw JSONB NOT NULL, loaded_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
        with Path(path).open(encoding="utf-8") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip() or line.lstrip().startswith("//"):
                    continue
                try:
                    record = validate_record(json.loads(line))
                except (json.JSONDecodeError, ValueError) as exc:
                    log.warning("Skipping %s line %d: %s", path, line_no, exc)
                    continue
                conn.execute(f"""INSERT INTO {schema}.x_posts
                    (tweet_id, author_id, author_handle, author_name, text, created_at, lang, url, query, raw)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (tweet_id) DO UPDATE SET author_id=EXCLUDED.author_id, author_handle=EXCLUDED.author_handle,
                    author_name=EXCLUDED.author_name, text=EXCLUDED.text, created_at=EXCLUDED.created_at,
                    lang=EXCLUDED.lang, url=EXCLUDED.url, query=EXCLUDED.query, raw=EXCLUDED.raw""",
                    (record["tweet_id"], record["author_id"], record["author_handle"], record["author_name"],
                     record["text"], record["created_at"], record["lang"], record["url"], record["query"], Jsonb(record["raw"])))
                count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="x_ingest")
    parser.add_argument("--path", default="data/x_collected/latest.jsonl")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        count = load(args.path)
    except Exception:
        log.exception("X JSONL import failed")
        return 1
    print(f"upserted {count} X posts from {args.path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
