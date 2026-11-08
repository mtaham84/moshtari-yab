"""Shared pytest fixtures: every storage test runs against a real PostgreSQL (with pgvector) in a throw-away schema.

Set TEST_DATABASE_URL (e.g. postgresql://postgres:pass@127.0.0.1:5432/postgres) or the POSTGRES_* variables.
In Docker: ``docker compose run --rm web python -m pytest``.
"""
from __future__ import annotations

import gc
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo


# Explicit test switches (production code no longer sniffs sys.argv / PYTEST_CURRENT_TEST).
os.environ.setdefault("INSTAGRAM_ANTIBAN_ENABLED", "false")   # no working-hours window or human pauses
os.environ.setdefault("ACCOUNTS_POOL_FILE_SYNC", "false")      # never rewrite the real accounts pool file
os.environ.setdefault("X_MAX_AGE_DAYS", "0")                    # fixtures contain old tweets
os.environ.setdefault("X_DEDUP_AGAINST_DB", "false")            # no dedup query unless a test passes a dsn


def pytest_configure(config):
    basetemp = config.option.basetemp
    if basetemp:
        # Pytest creates basetemp itself but not its missing parents.
        Path(basetemp).parent.mkdir(parents=True, exist_ok=True)


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
        if os.environ.get("REQUIRE_DB", "").lower() in {"1", "true", "yes", "on"}:
            pytest.fail(f"PostgreSQL is required (REQUIRE_DB) but not reachable: {exc}")
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


@pytest.fixture(scope="session", autouse=True)
def _test_switches():
    """Production code must not guess whether it runs under tests; switch these off explicitly. (Settings are imported
    by pytest-django before this module's environment defaults above, so set them directly.)"""
    from django.conf import settings

    settings.INSTAGRAM_ANTIBAN_ENABLED = False   # no working-hours window, no human pauses
    settings.ACCOUNTS_POOL_FILE_SYNC = False     # never rewrite the real accounts pool file


@pytest.fixture(scope="session")
def django_db_setup(django_db_setup):
    """A psycopg connection reachable only through a reference cycle (e.g. a traceback frame) stays open until the next
    GC run, and PostgreSQL refuses to drop the test database while another session is connected. Collect it first."""
    yield
    gc.collect()
