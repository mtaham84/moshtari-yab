from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_SECRET = re.compile(r"(?i)(bearer\s+|(?:api[_-]?key|token|password)=)[^\s&,;]+")


def _schema() -> str:
    schema = os.getenv("TG_DB_SCHEMA", "crawler")
    if not _IDENTIFIER.fullmatch(schema):
        raise ValueError("invalid crawler schema")
    return schema


def _dsn() -> str:
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
    from psycopg.conninfo import make_conninfo

    return make_conninfo(**{key: value for key, value in values.items() if value})


def _beat(source: str, *, items: int | None = None, error: str | None = None,
          cooldown_until: datetime | str | None = None) -> None:
    try:
        if source not in {"telegram", "x", "instagram"}:
            raise ValueError("unknown collector source")
        schema = _schema()
        table = f"{schema}.collector_heartbeats"
        message = _SECRET.sub(r"\1[redacted]", str(error or ""))[:240] or None
        import psycopg

        with psycopg.connect(_dsn(), autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} ("
                "source TEXT PRIMARY KEY, last_ok_at TIMESTAMPTZ, last_error_at TIMESTAMPTZ, "
                "last_error TEXT, consecutive_errors INTEGER NOT NULL DEFAULT 0, "
                "cooldown_until TIMESTAMPTZ, items_last_run BIGINT NOT NULL DEFAULT 0)"
            )
            if error is None:
                conn.execute(
                    f"INSERT INTO {table} (source,last_ok_at,last_error,last_error_at,consecutive_errors,"
                    "cooldown_until,items_last_run) VALUES (%s,now(),NULL,NULL,0,NULL,%s) "
                    "ON CONFLICT (source) DO UPDATE SET last_ok_at=now(),last_error=NULL,last_error_at=NULL,"
                    "consecutive_errors=0,cooldown_until=NULL,items_last_run=EXCLUDED.items_last_run",
                    (source, max(0, int(items or 0))),
                )
            else:
                conn.execute(
                    f"INSERT INTO {table} (source,last_error_at,last_error,consecutive_errors,cooldown_until) "
                    "VALUES (%s,now(),%s,1,%s) ON CONFLICT (source) DO UPDATE SET "
                    "last_error_at=now(),last_error=EXCLUDED.last_error,"
                    f"consecutive_errors={table}.consecutive_errors+1,"
                    "cooldown_until=COALESCE(EXCLUDED.cooldown_until,"
                    f"{table}.cooldown_until)",
                    (source, message, cooldown_until),
                )
    except Exception as exc:  # heartbeat failures must not stop collection
        log.warning("Could not write %s collector heartbeat (%s)", source, type(exc).__name__)


def beat_ok(source: str, items: int) -> None:
    _beat(source, items=items)


def beat_error(source: str, message: str, cooldown_until: datetime | str | None = None) -> None:
    _beat(source, error=message, cooldown_until=cooldown_until)


def read_heartbeats() -> list[dict[str, Any]]:
    try:
        import psycopg

        schema = _schema()
        with psycopg.connect(_dsn()) as conn:
            rows = conn.execute(
                f"SELECT source,last_ok_at,last_error_at,last_error,consecutive_errors,cooldown_until,items_last_run "
                f"FROM {schema}.collector_heartbeats ORDER BY source"
            ).fetchall()
        names = ("source", "last_ok_at", "last_error_at", "last_error",
                 "consecutive_errors", "cooldown_until", "items_last_run")
        return [dict(zip(names, row)) for row in rows]
    except Exception:
        log.debug("Could not read collector heartbeats", exc_info=True)
        return []
