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



def test_query_lead_success_and_10day_2strike_pruning_rule(tmp_path):
    path = tmp_path / "eval_test.sqlite3"
    store = SeenStore(path, platform="x")
    query = "خرید لباس مردانه"
    store.schedule([{"query": query, "kind": "product"}], budget=1)

    # Initially 0 strikes, not dropped
    stats = store.get_query_eval_stats(query)
    assert stats["strikes"] == 0
    assert stats["dropped"] == 0

    # 1. Record a successful lead discovery
    store.record_lead_success(query, leads_count=3)
    stats = store.get_query_eval_stats(query)
    assert stats["strikes"] == 0
    assert stats["dropped"] == 0
    assert stats["last_lead_at"] is not None
    assert store.last_lead_time(query) is not None

    # 2. First evaluation failure after 10 days of no leads
    strikes = store.record_evaluation_failure(query, heavy_beta=3.0)
    assert strikes == 1
    stats = store.get_query_eval_stats(query)
    assert stats["strikes"] == 1
    assert stats["dropped"] == 0
    assert stats["beta"] >= 4.0
    assert query not in store.dropped_queries()

    # 3. Second evaluation failure -> 2 strikes -> pruned!
    strikes = store.record_evaluation_failure(query, heavy_beta=3.0)
    assert strikes == 2
    stats = store.get_query_eval_stats(query)
    assert stats["strikes"] == 2
    assert stats["dropped"] == 1
    assert query in store.dropped_queries()


def test_account_pool_least_loaded_and_fair_selection():
    from workers.x_collector.account_pool import TwitterAccount, TwitterAccountPool
    pool = TwitterAccountPool.__new__(TwitterAccountPool)
    pool.accounts = [
        TwitterAccount(id=1, username="acc_busy", password="", auth_token="t1", ct0="c1", proxy_url="", status="ACTIVE", daily_actions=10),
        TwitterAccount(id=2, username="ebid8f", password="", auth_token="t2", ct0="c2", proxy_url="", status="ACTIVE", daily_actions=0),
        TwitterAccount(id=3, username="ksdjcuas", password="", auth_token="t3", ct0="c3", proxy_url="", status="ACTIVE", daily_actions=0),
    ]

    # Should pick one of the zero-action accounts first
    picked1 = pool.get_next_account()
    assert picked1.username in ("ebid8f", "ksdjcuas")
    picked1.daily_actions += 1

    # Next pick should be the other zero-action account
    picked2 = pool.get_next_account()
    assert picked2.username in ("ebid8f", "ksdjcuas")
    assert picked2.username != picked1.username


def test_enforce_daily_unique_schedule(tmp_path):
    import sqlite3
    from contextlib import closing
    from datetime import datetime, timezone, timedelta
    from shared.collector_kit.seen import SeenStore

    store = SeenStore(tmp_path / "daily_uniq.sqlite3", platform="x")
    queries = [
        {"query": "خرید لباس", "kind": "problem"},
        {"query": "پیراهن مردانه", "kind": "solution"},
    ]

    # Day 1 initial schedule
    sched1 = store.schedule(queries, budget=2, enforce_daily_unique=True)
    assert len(sched1) == 2

    # Simulate query 1 ran today
    now = datetime.now(timezone.utc)
    store.record_query("خرید لباس", fetched=10, new_posts=2, started_at=now)

    # Calling schedule again today with enforce_daily_unique=True should exclude "خرید لباس"
    sched2 = store.schedule(queries, budget=2, enforce_daily_unique=True)
    assert len(sched2) == 1
    assert sched2[0]["query"] == "پیراهن مردانه"

    # Simulate tomorrow: update last_run_at to yesterday
    yesterday = (now - timedelta(days=1)).isoformat()
    with closing(sqlite3.connect(store.path)) as db:
        db.execute("UPDATE query_runs SET last_run_at=? WHERE query='خرید لباس'", (yesterday,))
        db.commit()

    # On next day, "خرید لباس" returns to eligible pool!
    sched3 = store.schedule(queries, budget=2, enforce_daily_unique=True)
    assert len(sched3) == 2

