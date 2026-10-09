"""Shared pytest fixtures: every storage test runs against a real PostgreSQL (with pgvector) in a throw-away schema.

Set TEST_DATABASE_URL (e.g. postgresql://postgres:pass@127.0.0.1:5432/postgres) or the POSTGRES_* variables.
In Docker: ``docker compose run --rm web python -m pytest``.
"""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg.conninfo import make_conninfo


def _dsn() -> str:
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    params = {"host": os.environ.get("POSTGRES_HOST", "127.0.0.1"), "port": os.environ.get("POSTGRES_PORT", "5432"),
              "dbname": os.environ.get("POSTGRES_DB", "customer_yab"), "user": os.environ.get("POSTGRES_USER", "postgres"),
              "password": os.environ.get("POSTGRES_PASSWORD", "")}
    return make_conninfo(**{k: v for k, v in params.items() if v})


@pytest.fixture(scope="session")
def pg_dsn() -> str:
    dsn = _dsn()
    try:
        psycopg.connect(dsn, connect_timeout=3).close()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"PostgreSQL is not reachable ({exc.__class__.__name__}); set TEST_DATABASE_URL")
    return dsn


@pytest.fixture
def pg_schema(pg_dsn):
    """Unique schema name; dropped after the test."""
    names: list[str] = []

    def make(prefix: str = "t") -> str:
        names.append(f"{prefix}_{uuid.uuid4().hex[:10]}")
        return names[-1]

    yield make
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        for n in names:
            conn.execute(f"DROP SCHEMA IF EXISTS {n} CASCADE")
