import sqlite3
from datetime import date, datetime, timezone

from shared.collector_kit.seen import SeenStore


def test_platform_state_is_isolated_in_a_shared_database(tmp_path):
    path = tmp_path / "state.sqlite3"
    x = SeenStore(path, platform="x")
    instagram = SeenStore(path, platform="instagram")
    tweet = {"id": "123", "text": "متن نمونه"}

    assert x.add(tweet, daily_cap=10)
    assert x.has_seen(tweet)
    assert not instagram.has_seen(tweet)
    assert x.count_today() == 1
    assert instagram.count_today() == 0

    x.set_setting("rate_limit_until", "2099-01-01T00:00:00+00:00")
    assert x.cooldown_until() is not None
    assert instagram.cooldown_until() is None

    x.schedule([{"query": "آسیاب", "kind": "product"}], budget=1)
    x.record_query("آسیاب", fetched=1, new_posts=1, started_at=datetime.now(timezone.utc))
    instagram.schedule([{"query": "آسیاب", "kind": "product"}], budget=1)
    assert x.last_ok("آسیاب") is not None
    assert instagram.last_ok("آسیاب") is None

    instagram.set_seed_progress("account:demo", "2026-10-01T00:00:00+00:00")
    instagram.mark_comments_fetched("abc123")
    assert instagram.get_seed_progress("account:demo") is not None
    assert instagram.comments_were_fetched("abc123")
    assert not x.comments_were_fetched("abc123")

    assert SeenStore(path).platform == "instagram"


def test_legacy_x_state_is_migrated_without_losing_history(tmp_path):
    path = tmp_path / "legacy-x.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE seen_tweets (tweet_key TEXT PRIMARY KEY, first_seen TEXT NOT NULL)")
        db.execute("CREATE TABLE daily_counts (day TEXT PRIMARY KEY, collected INTEGER NOT NULL)")
        db.execute("CREATE TABLE collector_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE query_runs (query TEXT PRIMARY KEY, kind TEXT NOT NULL, priority REAL NOT NULL DEFAULT 1, last_run_at TEXT, runs INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0, new_posts INTEGER NOT NULL DEFAULT 0, last_error TEXT, last_ok_at TEXT)")
        db.execute("INSERT INTO daily_counts VALUES(?, 7)", (date.today().isoformat(),))
        db.execute("INSERT INTO collector_settings VALUES('rate_limit_until', '2099-01-01T00:00:00+00:00')")
        db.execute("INSERT INTO query_runs(query,kind,last_ok_at) VALUES('آسیاب','product','2026-10-16T00:00:00+00:00')")

    store = SeenStore(path, platform="x")

    assert store.count_today() == 7
    assert store.cooldown_until() == "2099-01-01T00:00:00+00:00"
    assert store.last_ok("آسیاب") == datetime(2026, 10, 16, tzinfo=timezone.utc)
    assert SeenStore(path, platform="instagram").count_today() == 0
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(daily_counts)")}
        assert "platform" in columns
        assert db.execute("SELECT platform, collected FROM daily_counts").fetchone() == ("x", 7)


def test_legacy_instagram_state_is_migrated_to_instagram(tmp_path):
    path = tmp_path / "legacy-instagram.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE seen_items (kind TEXT NOT NULL, item_id TEXT NOT NULL, first_seen TEXT NOT NULL, PRIMARY KEY(kind,item_id))")
        db.execute("CREATE TABLE daily_counts (day TEXT PRIMARY KEY, collected INTEGER NOT NULL)")
        db.execute("CREATE TABLE collector_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE seed_progress (seed TEXT PRIMARY KEY, last_seen_at TEXT, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE comments_fetched (post_shortcode TEXT PRIMARY KEY, fetched_at TEXT NOT NULL)")
        db.execute("INSERT INTO seen_items VALUES('post','ig:abc','2026-10-01T00:00:00+00:00')")
        db.execute("INSERT INTO daily_counts VALUES(?, 3)", (date.today().isoformat(),))
        db.execute("INSERT INTO collector_settings VALUES('rate_limit_until', '2099-01-01T00:00:00+00:00')")
        db.execute("INSERT INTO seed_progress VALUES('account:demo','2026-10-01T00:00:00+00:00','2026-10-01T00:00:00+00:00')")
        db.execute("INSERT INTO comments_fetched VALUES('abc','2026-10-01T00:00:00+00:00')")

    store = SeenStore(path, platform="instagram")

    assert store.count_today() == 3
    assert store.cooldown_until() == "2099-01-01T00:00:00+00:00"
    assert store.get_seed_progress("account:demo") == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert store.comments_were_fetched("abc")
    assert store.contains("post", "ig:abc")
    assert SeenStore(path, platform="x").count_today() == 0
    assert SeenStore(path).platform == "instagram"


def test_empty_legacy_instagram_state_keeps_its_platform(tmp_path):
    path = tmp_path / "empty-legacy-instagram.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE seen_items (kind TEXT NOT NULL, item_id TEXT NOT NULL, first_seen TEXT NOT NULL, PRIMARY KEY(kind,item_id))")
        db.execute("CREATE TABLE daily_counts (day TEXT PRIMARY KEY, collected INTEGER NOT NULL)")
        db.execute("CREATE TABLE collector_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE seed_progress (seed TEXT PRIMARY KEY, last_seen_at TEXT, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE comments_fetched (post_shortcode TEXT PRIMARY KEY, fetched_at TEXT NOT NULL)")
        db.execute("INSERT INTO daily_counts VALUES(?, 3)", (date.today().isoformat(),))
        db.execute("INSERT INTO collector_settings VALUES('rate_limit_until', '2099-01-01T00:00:00+00:00')")

    store = SeenStore(path)

    assert store.platform == "instagram"
    assert store.count_today() == 3
    assert store.cooldown_until() == "2099-01-01T00:00:00+00:00"


def test_legacy_x_store_keeps_default_platform(tmp_path):
    path = tmp_path / "legacy-x-default.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE seen_tweets (tweet_key TEXT PRIMARY KEY, first_seen TEXT NOT NULL)")
        db.execute("CREATE TABLE daily_counts (day TEXT PRIMARY KEY, collected INTEGER NOT NULL)")
        db.execute("CREATE TABLE collector_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("CREATE TABLE query_runs (query TEXT PRIMARY KEY, kind TEXT NOT NULL, priority REAL NOT NULL DEFAULT 1, last_run_at TEXT, runs INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0, new_posts INTEGER NOT NULL DEFAULT 0, last_error TEXT, last_ok_at TEXT)")
        db.execute("INSERT INTO daily_counts VALUES(?, 5)", (date.today().isoformat(),))

    store = SeenStore(path)

    assert store.platform == "x"
    assert store.count_today() == 5
