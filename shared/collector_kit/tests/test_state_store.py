from __future__ import annotations

import os
from datetime import date, datetime, timezone
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
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    yield from _postgres_state_store(dsn, platform)


@pytest.fixture
def postgres_state_store():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not set")
    yield from _postgres_state_store(dsn, "test_postgres")


def _postgres_state_store(dsn, platform):
    import psycopg

    schema = "test_state_store_" + uuid4().hex
    try:
        with psycopg.connect(dsn):
            pass
    except Exception as exc:
        pytest.skip(f"PostgreSQL is not reachable ({type(exc).__name__})")
    store = PostgresStateStore(platform=platform, dsn=dsn, schema=schema)
    try:
        yield store
    finally:
        with psycopg.connect(dsn, autocommit=True) as db:
            db.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_state_store_tracks_seen_ids_daily_counts_and_cooldown(state_store):
    record = {"id": "state-store-record", "text": "sample"}
    assert not state_store.has_seen(record)
    assert state_store.add(record, daily_cap=3)
    assert not state_store.add(record, daily_cap=3)
    assert state_store.has_seen(record)
    assert state_store.count_today(date.today()) == 1

    state_store.set_setting("rate_limit_until", "2099-01-01T00:00:00+00:00")
    assert state_store.cooldown_until() == "2099-01-01T00:00:00+00:00"


def test_state_store_deduplicates_item_batches(state_store):
    rows = [{"id": "comment-1", "kind": "comment"},
            {"id": "comment-1", "kind": "comment"}]
    state_store.add_many(rows)
    state_store.add_many(rows)

    assert state_store.contains("comment", "comment-1")
    assert state_store.count_items("comment") == 1
    assert state_store.count_today() == 1


def test_state_store_schedules_and_records_query_success(state_store):
    started = datetime.now(timezone.utc)
    scheduled = state_store.schedule([
        {"query": "buyer intent", "kind": "intent", "priority": 1.0},
        {"query": "catalog item", "kind": "product", "priority": 1.0},
    ], budget=1)
    assert scheduled[0]["query"] == "buyer intent"
    state_store.record_query("buyer intent", fetched=5, new_posts=2, started_at=started)

    assert state_store.last_ok("buyer intent") is not None
    assert state_store.query_status()[0]["fetched"] == 5


def test_state_store_tracks_seed_progress_and_fetched_comments(state_store):
    state_store.set_seed_progress("account:sample", "2026-10-16T00:00:00+00:00")
    state_store.mark_comments_fetched("post-1")

    assert state_store.get_seed_progress("account:sample") is not None
    assert state_store.comments_were_fetched("post-1")


def test_postgres_state_import_does_not_inflate_daily_counts(postgres_state_store):
    state = {
        "seen_tweets": [],
        "seen_items": [("comment", "comment-1", "2026-10-16T00:00:00+00:00")],
        "daily_counts": [(date.today().isoformat(), 1)],
        "collector_settings": [],
        "query_runs": [],
        "seed_progress": [],
        "comments_fetched": [],
    }

    postgres_state_store.import_missing_state(state)
    postgres_state_store.import_missing_state(state)

    assert postgres_state_store.count_today() == 1
