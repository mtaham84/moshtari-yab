from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class SeenStore:
    def __init__(self, path: str | Path, platform: str | None = None):
        self.path = Path(path)
        self.platform = platform or self._existing_platform()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS seen_tweets (tweet_key TEXT NOT NULL, first_seen TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'x', PRIMARY KEY(platform,tweet_key))")
            db.execute("CREATE TABLE IF NOT EXISTS seen_items (kind TEXT NOT NULL, item_id TEXT NOT NULL, first_seen TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'instagram', PRIMARY KEY(platform,kind,item_id))")
            db.execute("CREATE TABLE IF NOT EXISTS daily_counts (platform TEXT NOT NULL, day TEXT NOT NULL, collected INTEGER NOT NULL, PRIMARY KEY(platform,day))")
            db.execute("CREATE TABLE IF NOT EXISTS collector_settings (platform TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(platform,key))")
            db.execute("CREATE TABLE IF NOT EXISTS query_runs (query TEXT NOT NULL, kind TEXT NOT NULL, priority REAL NOT NULL DEFAULT 1, last_run_at TEXT, runs INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0, new_posts INTEGER NOT NULL DEFAULT 0, last_error TEXT, platform TEXT NOT NULL DEFAULT 'x', PRIMARY KEY(platform,query))")
            q_cols = {row[1] for row in db.execute("PRAGMA table_info(query_runs)")}
            if "last_ok_at" not in q_cols:
                db.execute("ALTER TABLE query_runs ADD COLUMN last_ok_at TEXT")
            if "alpha" not in q_cols:
                db.execute("ALTER TABLE query_runs ADD COLUMN alpha REAL NOT NULL DEFAULT 1.0")
            if "beta" not in q_cols:
                db.execute("ALTER TABLE query_runs ADD COLUMN beta REAL NOT NULL DEFAULT 1.0")
            if "leads_count" not in q_cols:
                db.execute("ALTER TABLE query_runs ADD COLUMN leads_count INTEGER NOT NULL DEFAULT 0")
            if "empty_runs" not in q_cols:
                db.execute("ALTER TABLE query_runs ADD COLUMN empty_runs INTEGER NOT NULL DEFAULT 0")
            if "dropped" not in q_cols:
                db.execute("ALTER TABLE query_runs ADD COLUMN dropped INTEGER NOT NULL DEFAULT 0")
            if "strikes" not in q_cols:
                db.execute("ALTER TABLE query_runs ADD COLUMN strikes INTEGER NOT NULL DEFAULT 0")
            if "last_lead_at" not in q_cols:
                db.execute("ALTER TABLE query_runs ADD COLUMN last_lead_at TEXT")
            if "eval_fetched" not in q_cols:
                db.execute("ALTER TABLE query_runs ADD COLUMN eval_fetched INTEGER NOT NULL DEFAULT 0")
            db.execute("CREATE TABLE IF NOT EXISTS thread_fetches (root_tweet_id TEXT PRIMARY KEY, fetched_count INTEGER NOT NULL DEFAULT 0, last_fetched_at TEXT, next_due_at TEXT, status TEXT NOT NULL DEFAULT 'pending', new_replies INTEGER NOT NULL DEFAULT 0)")
            db.execute("CREATE TABLE IF NOT EXISTS seed_progress (seed TEXT NOT NULL, last_seen_at TEXT, updated_at TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'instagram', PRIMARY KEY(platform,seed))")
            db.execute("CREATE TABLE IF NOT EXISTS comments_fetched (post_shortcode TEXT NOT NULL, fetched_at TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'instagram', PRIMARY KEY(platform,post_shortcode))")
            db.commit()
        self._upgrade_shared_tables()
        with closing(sqlite3.connect(self.path)) as db:
            for table in ("seed_progress", "comments_fetched"):
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if "platform" not in columns:
                    db.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
                    if table == "seed_progress":
                        db.execute("CREATE TABLE seed_progress (seed TEXT NOT NULL, last_seen_at TEXT, updated_at TEXT NOT NULL, platform TEXT NOT NULL, PRIMARY KEY(platform,seed))")
                        db.execute("INSERT INTO seed_progress(seed,last_seen_at,updated_at,platform) SELECT seed,last_seen_at,updated_at,'instagram' FROM seed_progress_legacy")
                    else:
                        db.execute("CREATE TABLE comments_fetched (post_shortcode TEXT NOT NULL, fetched_at TEXT NOT NULL, platform TEXT NOT NULL, PRIMARY KEY(platform,post_shortcode))")
                        db.execute("INSERT INTO comments_fetched(post_shortcode,fetched_at,platform) SELECT post_shortcode,fetched_at,'instagram' FROM comments_fetched_legacy")
                    db.execute(f"DROP TABLE {table}_legacy")
            query_columns = {row[1] for row in db.execute("PRAGMA table_info(query_runs)")}
            if "platform" not in query_columns:
                db.execute("ALTER TABLE query_runs RENAME TO query_runs_legacy")
                db.execute("CREATE TABLE query_runs (query TEXT NOT NULL, kind TEXT NOT NULL, priority REAL NOT NULL DEFAULT 1, last_run_at TEXT, runs INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0, new_posts INTEGER NOT NULL DEFAULT 0, last_error TEXT, last_ok_at TEXT, alpha REAL NOT NULL DEFAULT 1.0, beta REAL NOT NULL DEFAULT 1.0, leads_count INTEGER NOT NULL DEFAULT 0, empty_runs INTEGER NOT NULL DEFAULT 0, dropped INTEGER NOT NULL DEFAULT 0, platform TEXT NOT NULL DEFAULT 'x', PRIMARY KEY(platform,query))")
                db.execute("INSERT INTO query_runs(query,kind,priority,last_run_at,runs,fetched,new_posts,last_error,last_ok_at,alpha,beta,leads_count,empty_runs,dropped,platform) SELECT query,kind,priority,last_run_at,runs,fetched,new_posts,last_error,last_ok_at,alpha,beta,leads_count,empty_runs,dropped,'x' FROM query_runs_legacy")
                db.execute("DROP TABLE query_runs_legacy")
            db.commit()

    def _existing_platform(self) -> str:
        if not self.path.is_file():
            return "x"
        with closing(sqlite3.connect(self.path)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("comments_fetched", "seed_progress", "seen_items"):
                if table not in tables:
                    continue
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if "platform" not in columns:
                    return "instagram"
                if "platform" in columns:
                    row = db.execute(f"SELECT 1 FROM {table} WHERE platform='instagram' LIMIT 1").fetchone()
                if row:
                    return "instagram"
            if "daily_counts" in tables:
                columns = {row[1] for row in db.execute("PRAGMA table_info(daily_counts)")}
                if "platform" in columns:
                    row = db.execute("SELECT 1 FROM daily_counts WHERE platform='instagram' LIMIT 1").fetchone()
                    if row:
                        return "instagram"
            if "collector_settings" in tables:
                columns = {row[1] for row in db.execute("PRAGMA table_info(collector_settings)")}
                if "platform" in columns:
                    row = db.execute("SELECT 1 FROM collector_settings WHERE platform='instagram' LIMIT 1").fetchone()
                    if row:
                        return "instagram"
        return "x"

    def _upgrade_shared_tables(self) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            daily_columns = {row[1] for row in db.execute("PRAGMA table_info(daily_counts)")}
            if "platform" not in daily_columns:
                db.execute("ALTER TABLE daily_counts RENAME TO daily_counts_legacy")
                db.execute("CREATE TABLE daily_counts (platform TEXT NOT NULL, day TEXT NOT NULL, collected INTEGER NOT NULL, PRIMARY KEY(platform,day))")
                db.execute("INSERT INTO daily_counts(platform,day,collected) SELECT ?,day,collected FROM daily_counts_legacy", (self.platform,))
                db.execute("DROP TABLE daily_counts_legacy")
            settings_columns = {row[1] for row in db.execute("PRAGMA table_info(collector_settings)")}
            if "platform" not in settings_columns:
                db.execute("ALTER TABLE collector_settings RENAME TO collector_settings_legacy")
                db.execute("CREATE TABLE collector_settings (platform TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(platform,key))")
                db.execute("INSERT INTO collector_settings(platform,key,value) SELECT ?,key,value FROM collector_settings_legacy", (self.platform,))
                db.execute("DROP TABLE collector_settings_legacy")
            tweet_columns = {row[1] for row in db.execute("PRAGMA table_info(seen_tweets)")}
            if "platform" not in tweet_columns:
                db.execute("ALTER TABLE seen_tweets RENAME TO seen_tweets_legacy")
                db.execute("CREATE TABLE seen_tweets (tweet_key TEXT NOT NULL, first_seen TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'x', PRIMARY KEY(platform,tweet_key))")
                db.execute("INSERT INTO seen_tweets(tweet_key,first_seen,platform) SELECT tweet_key,first_seen,'x' FROM seen_tweets_legacy")
                db.execute("DROP TABLE seen_tweets_legacy")
            item_columns = {row[1] for row in db.execute("PRAGMA table_info(seen_items)")}
            if "platform" not in item_columns:
                db.execute("ALTER TABLE seen_items RENAME TO seen_items_legacy")
                db.execute("CREATE TABLE seen_items (kind TEXT NOT NULL, item_id TEXT NOT NULL, first_seen TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'instagram', PRIMARY KEY(platform,kind,item_id))")
                db.execute("INSERT INTO seen_items(kind,item_id,first_seen,platform) SELECT kind,item_id,first_seen,'instagram' FROM seen_items_legacy")
                db.execute("DROP TABLE seen_items_legacy")
            db.commit()

    @staticmethod
    def _key(record: dict[str, Any]) -> str:
        if record.get("id"):
            value = "id:" + str(record["id"]) + (f"|p:{record['product_id']}" if record.get("product_id") else "")
        else:
            value = "text:" + str(record.get("text", ""))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def has_seen(self, record: dict[str, Any]) -> bool:
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT 1 FROM seen_tweets WHERE tweet_key=? AND platform=?", (self._key(record), self.platform)).fetchone() is not None

    def add(self, record: dict[str, Any], daily_cap: int) -> bool:
        today = date.today().isoformat()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT collected FROM daily_counts WHERE platform=? AND day=?", (self.platform, today)).fetchone()
            if row and row[0] >= daily_cap:
                db.rollback()
                return False
            cursor = db.execute("INSERT OR IGNORE INTO seen_tweets(tweet_key,first_seen,platform) VALUES(?,?,?)",
                                (self._key(record), datetime.now(timezone.utc).isoformat(), self.platform))
            if cursor.rowcount:
                db.execute("INSERT INTO daily_counts(platform,day,collected) VALUES(?,?,1) ON CONFLICT(platform,day) DO UPDATE SET collected=collected+1", (self.platform, today))
            db.commit()
            return bool(cursor.rowcount)

    def contains(self, kind: str, item_id: str) -> bool:
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT 1 FROM seen_items WHERE kind=? AND item_id=? AND platform=?", (kind, item_id, self.platform)).fetchone() is not None

    def add_many(self, records: list[dict[str, Any]]) -> None:
        now, today = datetime.now(timezone.utc).isoformat(), date.today().isoformat()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN IMMEDIATE")
            inserted = 0
            for row in records:
                cursor = db.execute("INSERT OR IGNORE INTO seen_items(kind,item_id,first_seen,platform) VALUES(?,?,?,?)",
                                    (row["kind"], row["id"], now, self.platform))
                inserted += cursor.rowcount
            if inserted:
                db.execute("INSERT INTO daily_counts(platform,day,collected) VALUES(?,?,?) ON CONFLICT(platform,day) DO UPDATE SET collected=collected+excluded.collected",
                           (self.platform, today, inserted))
            db.commit()

    def count_today(self, day: date | None = None) -> int:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT collected FROM daily_counts WHERE platform=? AND day=?", (self.platform, (day or date.today()).isoformat())).fetchone()
        return int(row[0]) if row else 0

    def count_items(self, kind: str) -> int:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT COUNT(*) FROM seen_items WHERE kind=? AND platform=?",
                             (kind, self.platform)).fetchone()
        return int(row[0])

    def query_status(self) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT query,kind,last_run_at,fetched,new_posts,empty_runs,dropped FROM query_runs WHERE platform=? ORDER BY query",
                              (self.platform,)).fetchall()
        names = ("query", "kind", "last_run_at", "fetched", "new_posts", "empty_runs", "dropped")
        return [dict(zip(names, row)) for row in rows]

    def export_state(self) -> dict[str, list[tuple]]:
        with closing(sqlite3.connect(self.path)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            exports = {
                "seen_tweets": db.execute("SELECT tweet_key,first_seen FROM seen_tweets WHERE platform=?",
                                             (self.platform,)).fetchall(),
                "seen_items": db.execute("SELECT kind,item_id,first_seen FROM seen_items WHERE platform=?",
                                            (self.platform,)).fetchall(),
                "daily_counts": db.execute("SELECT day,collected FROM daily_counts WHERE platform=?",
                                              (self.platform,)).fetchall(),
                "collector_settings": db.execute("SELECT key,value FROM collector_settings WHERE platform=?",
                                                   (self.platform,)).fetchall(),
                "query_runs": db.execute("SELECT query,kind,priority,last_run_at,runs,fetched,new_posts,last_error,last_ok_at "
                                          "FROM query_runs WHERE platform=?", (self.platform,)).fetchall() if "query_runs" in tables else [],
                "seed_progress": db.execute("SELECT seed,last_seen_at,updated_at FROM seed_progress WHERE platform=?",
                                               (self.platform,)).fetchall() if "seed_progress" in tables else [],
                "comments_fetched": db.execute("SELECT post_shortcode,fetched_at FROM comments_fetched WHERE platform=?",
                                                  (self.platform,)).fetchall() if "comments_fetched" in tables else [],
            }
        return exports

    def increment_today(self, amount: int, day: date | None = None) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO daily_counts(platform,day,collected) VALUES(?,?,?) ON CONFLICT(platform,day) DO UPDATE SET collected=collected+excluded.collected",
                       (self.platform, (day or date.today()).isoformat(), amount))
            db.commit()

    def get_setting(self, key: str) -> str | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT value FROM collector_settings WHERE platform=? AND key=?", (self.platform, key)).fetchone()
        return row[0] if row else None

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
        until = (now or datetime.now(timezone.utc)) + timedelta(seconds=seconds)
        value = until.isoformat()
        self.set_setting("rate_limit_until", value)
        return value

    def set_setting(self, key: str, value: str) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO collector_settings(platform,key,value) VALUES(?,?,?) ON CONFLICT(platform,key) DO UPDATE SET value=excluded.value",
                       (self.platform, key, value))
            db.commit()

    def schedule(self, queries: list[dict[str, Any]], budget: int, enforce_daily_unique: bool = False) -> list[dict[str, Any]]:
        import random
        now = datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.path)) as db:
            for item in queries:
                db.execute("INSERT INTO query_runs(query,kind,priority,platform) VALUES(?,?,?,?) ON CONFLICT(platform,query) DO UPDATE SET kind=excluded.kind,priority=excluded.priority",
                           (item["query"], item.get("kind", "product"), item.get("priority", 1.0), self.platform))
            rows = db.execute("SELECT query,kind,priority,last_run_at,runs,alpha,beta,leads_count,dropped FROM query_runs WHERE platform=? AND query IN (%s)" % ",".join("?" for _ in queries),
                              [self.platform, *[item["query"] for item in queries]]).fetchall() if queries else []
            by_query = {row[0]: row for row in rows}

            def score(item):
                row = by_query[item["query"]]
                last, kind = row[3], row[1]
                priority = float(row[2])
                kind_wt = {"intent": 1.0, "problem": 0.8, "alternative": 0.7, "complaint": 0.6, "product": 0.4}.get(kind, 0.8)
                if not last or row[4] == 0:
                    return -(1e12 * priority * kind_wt), item["query"]
                stale = max(1.0, (now - datetime.fromisoformat(last)).total_seconds())
                alpha = max(0.1, float(row[5] if row[5] is not None else 1.0))
                beta = max(0.1, float(row[6] if row[6] is not None else 1.0))
                # Thompson Sampling from Beta(alpha, beta)
                theta = random.betavariate(alpha, beta)
                return -(stale * priority * kind_wt * (0.2 + 0.8 * theta)), item["query"]

            eligible = [item for item in queries if not by_query[item["query"]][8]]
            if enforce_daily_unique:
                today_date = now.date()
                eligible = [
                    item for item in eligible
                    if not by_query[item["query"]][3]
                    or datetime.fromisoformat(by_query[item["query"]][3]).date() != today_date
                ]
            selected = sorted(eligible, key=score)[:max(0, budget)]
            db.commit()
            return selected

    def record_feedback(self, query: str, success: bool, weight: float = 1.0) -> None:
        """Reward real buyers and suppress phrases repeatedly judged irrelevant by sellers."""
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO query_runs(platform, query, kind, alpha, beta, leads_count) VALUES(?, ?, 'product', 1.0, 1.0, 0) ON CONFLICT(platform, query) DO NOTHING",
                       (self.platform, query))
            if success:
                db.execute("UPDATE query_runs SET alpha = alpha + ?, leads_count = leads_count + 1, dropped = 0 WHERE platform=? AND query=?",
                           (weight, self.platform, query))
            else:
                row = db.execute("SELECT alpha, beta FROM query_runs WHERE platform=? AND query=?",
                                 (self.platform, query)).fetchone()
                alpha, beta = float(row[0] or 1), float(row[1] or 1)
                beta += float(weight)
                dropped = int(beta >= 3.0 and beta >= 2.0 * alpha)
                db.execute("UPDATE query_runs SET beta=?, dropped=CASE WHEN ?=1 THEN 1 ELSE dropped END WHERE platform=? AND query=?",
                           (beta, dropped, self.platform, query))
            db.commit()

    def get_query_bandit_stats(self, query: str) -> dict[str, Any]:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT alpha, beta, leads_count, runs, fetched FROM query_runs WHERE platform=? AND query=?",
                             (self.platform, query)).fetchone()
            if row:
                return {"alpha": row[0], "beta": row[1], "leads": row[2], "runs": row[3], "fetched": row[4]}
            return {"alpha": 1.0, "beta": 1.0, "leads": 0, "runs": 0, "fetched": 0}

    def record_query(self, query: str, fetched: int, new_posts: int, error: str | None = None,
                     started_at: datetime | None = None) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE query_runs SET last_run_at=?,runs=runs+1,fetched=fetched+?,new_posts=new_posts+?,last_error=?,eval_fetched=eval_fetched+? WHERE platform=? AND query=?",
                       (datetime.now(timezone.utc).isoformat(), fetched, new_posts, error, fetched, self.platform, query))
            if error is None and started_at is not None:
                db.execute("UPDATE query_runs SET last_ok_at=? WHERE platform=? AND query=?", (started_at.isoformat(), self.platform, query))
            if error is None and fetched == 0:
                db.execute("UPDATE query_runs SET beta=coalesce(beta,1.0)+0.5, empty_runs=empty_runs+1, dropped=CASE WHEN empty_runs+1>=6 THEN 1 ELSE dropped END WHERE platform=? AND query=?",
                           (self.platform, query))
            elif error is None and fetched > 0:
                db.execute("UPDATE query_runs SET empty_runs=0 WHERE platform=? AND query=?", (self.platform, query))
            db.commit()

    def record_lead_success(self, query: str, leads_count: int = 1) -> None:
        """Called when a query yields qualified leads. Resets evaluation counter and strikes."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""
                UPDATE query_runs
                SET last_lead_at = ?,
                    strikes = 0,
                    eval_fetched = 0,
                    empty_runs = 0,
                    leads_count = leads_count + ?,
                    alpha = alpha + 1.0,
                    dropped = 0
                WHERE platform = ? AND query = ?
            """, (now_iso, leads_count, self.platform, query))
            db.commit()

    def record_evaluation_failure(self, query: str, heavy_beta: float = 3.0) -> int:
        """Applies heavy negative penalty and increments strikes when 10 days pass with 80 inspected posts and 0 leads.
        If strikes >= 2, marks query as dropped (pruned)."""
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT strikes, beta, alpha FROM query_runs WHERE platform = ? AND query = ?",
                             (self.platform, query)).fetchone()
            if not row:
                return 0
            strikes = (row[0] or 0) + 1
            beta = float(row[1] or 1.0) + heavy_beta
            dropped = 1 if strikes >= 2 else 0
            db.execute("""
                UPDATE query_runs
                SET strikes = ?,
                    beta = ?,
                    dropped = CASE WHEN ? = 1 THEN 1 ELSE dropped END,
                    eval_fetched = 0
                WHERE platform = ? AND query = ?
            """, (strikes, beta, dropped, self.platform, query))
            db.commit()
            return strikes

    def get_query_eval_stats(self, query: str) -> dict[str, Any]:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("""
                SELECT last_lead_at, last_ok_at, strikes, eval_fetched, runs, alpha, beta, dropped
                FROM query_runs WHERE platform = ? AND query = ?
            """, (self.platform, query)).fetchone()
        if not row:
            return {"last_lead_at": None, "last_ok_at": None, "strikes": 0, "eval_fetched": 0, "runs": 0, "alpha": 1.0, "beta": 1.0, "dropped": 0}
        return {
            "last_lead_at": row[0],
            "last_ok_at": row[1],
            "strikes": row[2] or 0,
            "eval_fetched": row[3] or 0,
            "runs": row[4] or 0,
            "alpha": row[5] or 1.0,
            "beta": row[6] or 1.0,
            "dropped": row[7] or 0,
        }

    def last_lead_time(self, query: str) -> datetime | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT last_lead_at, last_ok_at FROM query_runs WHERE platform = ? AND query = ?",
                             (self.platform, query)).fetchone()
        if not row:
            return None
        val = row[0] or row[1]
        if not val:
            return None
        try:
            dt = datetime.fromisoformat(val)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    def dropped_queries(self, limit: int = 50) -> list[str]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT query FROM query_runs WHERE platform=? AND dropped=1 ORDER BY empty_runs DESC, query LIMIT ?",
                              (self.platform, max(0, limit))).fetchall()
        return [str(row[0]) for row in rows]

    def last_ok(self, query: str) -> datetime | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT last_ok_at FROM query_runs WHERE platform=? AND query=?", (self.platform, query)).fetchone()
        try:
            return datetime.fromisoformat(row[0]) if row and row[0] else None
        except ValueError:
            return None


    def get_seed_progress(self, seed: str) -> datetime | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT last_seen_at FROM seed_progress WHERE platform=? AND seed=?", (self.platform, seed)).fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def set_seed_progress(self, seed: str, latest: str | None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO seed_progress(seed,last_seen_at,updated_at,platform) VALUES(?,?,?,?) ON CONFLICT(platform,seed) DO UPDATE SET last_seen_at=COALESCE(excluded.last_seen_at,seed_progress.last_seen_at),updated_at=excluded.updated_at",
                       (seed, latest, now, self.platform))
            db.commit()

    def comments_were_fetched(self, shortcode: str) -> bool:
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT 1 FROM comments_fetched WHERE platform=? AND post_shortcode=?", (self.platform, shortcode)).fetchone() is not None

    def mark_comments_fetched(self, shortcode: str) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT OR IGNORE INTO comments_fetched(post_shortcode,fetched_at,platform) VALUES(?,?,?)",
                       (shortcode, datetime.now(timezone.utc).isoformat(), self.platform))
            db.commit()
