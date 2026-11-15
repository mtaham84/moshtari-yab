"""Storage tests share real SQLite and isolated PostgreSQL backends."""
from uuid import uuid4

import pytest

from shared.collector_kit.seen import SeenStore
from shared.collector_kit.state_store import PostgresStateStore


@pytest.fixture(params=["sqlite", "postgres"])
def state_store(request, tmp_path):
    platform = "test_" + request.param
    if request.param == "sqlite":
        yield SeenStore(tmp_path / "state.sqlite3", platform=platform)
        return
    yield from _postgres_state_store(request.getfixturevalue("pg_dsn"), platform)


@pytest.fixture
def postgres_state_store(pg_dsn):
    yield from _postgres_state_store(pg_dsn, "test_postgres")


def _postgres_state_store(dsn, platform):
    import psycopg

    schema = "test_state_store_" + uuid4().hex
    store = PostgresStateStore(platform=platform, dsn=dsn, schema=schema)
    try:
        yield store
    finally:
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
