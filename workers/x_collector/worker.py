from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import random
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import cli_mapping as mapping
from .client import XCliClient, XCliError
from need_engine.x_filters import PROMOTIONAL_PATTERNS, is_promotional_post

log = logging.getLogger("x_collector")
RATE_LIMIT_COOLDOWN_SECONDS = 15 * 60
PROMOTIONAL_PATTERNS = (
    r"(?:buy now|shop now|limited offer|order now|use code|discount|promo code|sponsored|giveaway)",
    r"(?:تخفیف|فروش ویژه|ثبت سفارش|سفارش دهید|خرید کنید|همین حالا بخرید|ارسال رایگان|کد تخفیف|فروش فوری|موجود شد|برای خرید|دایرکت بدهید|دایرکت دهید|تماس بگیرید|قیمت ویژه)",
)


def _first(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_tweet(raw: dict[str, Any], query: str | None = None) -> dict[str, Any] | None:
    if raw.get("isRetweet") is True:
        log.info("Skipping retweet %s", raw.get("id") or "without id")
        return None
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    tweet_id = _first(raw, mapping.OUTPUT_ID_KEYS)
    text = _first(raw, mapping.OUTPUT_TEXT_KEYS)
    handle = _first(raw, mapping.OUTPUT_HANDLE_KEYS) or author.get("screenName")
    author_id = _first(raw, mapping.OUTPUT_AUTHOR_ID_KEYS) or author.get("id")
    if not tweet_id or not author_id or not handle or not isinstance(text, str) or not text.strip():
        missing = []
        if not tweet_id:
            missing.append("id")
        if not handle:
            missing.append("handle")
        if not author_id:
            missing.append("author_id")
        if not isinstance(text, str) or not text.strip():
            missing.append("text")
        log.warning("Skipping tweet with missing required fields: %s", ", ".join(missing))
        return None
    if is_promotional_post(text):
        log.info("Skipping promotional X post %s", tweet_id)
        return None
    clean_id = str(tweet_id).strip()
    if not clean_id:
        return None
    if handle:
        handle = str(handle).strip().lstrip("@").split("/")[0]
    tweet_url = f"https://x.com/{handle}/status/{clean_id}" if handle else None
    created_at = _normalize_created_at(_first(raw, mapping.OUTPUT_CREATED_KEYS))
    return {
        "id": clean_id,
        "source": "x",
        "text": text.strip(),
        "author_handle": f"@{handle}" if handle else None,
        "author_name": _first(raw, mapping.OUTPUT_NAME_KEYS) or author.get("name"),
        "author_id": str(author_id),
        "author_verified": bool(author.get("verified") or author.get("isVerified") or raw.get("author_verified")),
        "author_bio": str(author.get("description") or author.get("bio") or "")[:500],
        "created_at": str(created_at) if created_at else None,
        "url": tweet_url,
        "metadata": {"collector": "agent-reach-cli", "raw_cli": raw, "lang": _first(raw, mapping.OUTPUT_LANG_KEYS), "query": query},
    }


def _normalize_created_at(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.replace(".", "", 1).isdigit()):
            epoch = float(value)
            if epoch > 10_000_000_000:
                epoch /= 1000
            parsed = datetime.fromtimestamp(epoch, tz=timezone.utc)
        elif isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value).strip()
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                parsed = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (OverflowError, OSError, TypeError, ValueError):
        log.warning("Unparseable tweet created_at value; writing null")
        return None


class SeenStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS seen_tweets (tweet_key TEXT PRIMARY KEY, first_seen TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS daily_counts (day TEXT PRIMARY KEY, collected INTEGER NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS collector_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS query_runs (query TEXT PRIMARY KEY, kind TEXT NOT NULL, priority REAL NOT NULL DEFAULT 1, last_run_at TEXT, runs INTEGER NOT NULL DEFAULT 0, fetched INTEGER NOT NULL DEFAULT 0, new_posts INTEGER NOT NULL DEFAULT 0, last_error TEXT)")
            if "last_ok_at" not in {row[1] for row in connection.execute("PRAGMA table_info(query_runs)")}:
                connection.execute("ALTER TABLE query_runs ADD COLUMN last_ok_at TEXT")   # start of the last successful search
            connection.execute("CREATE TABLE IF NOT EXISTS thread_fetches (root_tweet_id TEXT PRIMARY KEY, fetched_count INTEGER NOT NULL DEFAULT 0, last_fetched_at TEXT, next_due_at TEXT, status TEXT NOT NULL DEFAULT 'pending', new_replies INTEGER NOT NULL DEFAULT 0)")
            connection.commit()

    @staticmethod
    def _key(record: dict[str, Any]) -> str:
        if record.get("id"):
            value = "id:" + str(record["id"]) + (f"|p:{record['product_id']}" if record.get("product_id") else "")
        else:
            value = "text:" + str(record.get("text", ""))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def has_seen(self, record: dict[str, Any]) -> bool:
        with closing(sqlite3.connect(self.path)) as connection:
            return connection.execute("SELECT 1 FROM seen_tweets WHERE tweet_key=?", (self._key(record),)).fetchone() is not None

    def add(self, record: dict[str, Any], daily_cap: int) -> bool:
        day_key = date.today().isoformat()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT collected FROM daily_counts WHERE day=?", (day_key,)).fetchone()
            if row and row[0] >= daily_cap:
                connection.rollback()
                return False
            cursor = connection.execute("INSERT OR IGNORE INTO seen_tweets(tweet_key,first_seen) VALUES(?,?)", (self._key(record), datetime.now(timezone.utc).isoformat()))
            if cursor.rowcount:
                connection.execute("INSERT INTO daily_counts(day,collected) VALUES(?,1) ON CONFLICT(day) DO UPDATE SET collected=collected+1", (day_key,))
            connection.commit()
            return bool(cursor.rowcount)

    def count_today(self, day: date | None = None) -> int:
        day_key = (day or date.today()).isoformat()
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT collected FROM daily_counts WHERE day=?", (day_key,)).fetchone()
        return int(row[0]) if row else 0

    def increment_today(self, amount: int, day: date | None = None) -> None:
        day_key = (day or date.today()).isoformat()
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT INTO daily_counts(day,collected) VALUES(?,?) ON CONFLICT(day) DO UPDATE SET collected=collected+excluded.collected", (day_key, amount))
            connection.commit()

    def get_setting(self, key: str) -> str | None:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT value FROM collector_settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("INSERT INTO collector_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            connection.commit()

    def schedule(self, queries: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        with closing(sqlite3.connect(self.path)) as connection:
            for item in queries:
                connection.execute("INSERT INTO query_runs(query,kind,priority) VALUES(?,?,?) ON CONFLICT(query) DO UPDATE SET kind=excluded.kind,priority=excluded.priority",
                                   (item["query"], item.get("kind", "product"), item.get("priority", 1.0)))
            rows = connection.execute("SELECT query,kind,priority,last_run_at,runs FROM query_runs WHERE query IN (%s)" % ",".join("?" for _ in queries),
                                      [item["query"] for item in queries]).fetchall() if queries else []
            by_query = {row[0]: row for row in rows}
            def score(item):
                row = by_query[item["query"]]
                last, kind = row[3], row[1]
                stale = 1e12 if not last else max(1.0, (now - datetime.fromisoformat(last)).total_seconds())
                return -(stale * float(row[2]) * {"intent": 1.0, "problem": 0.8, "product": 0.4}.get(kind, 1.0)), item["query"]
            selected = sorted(queries, key=score)[:max(0, budget)]
            connection.commit()
            return selected

    def record_query(self, query: str, fetched: int, new_posts: int, error: str | None = None,
                     started_at: datetime | None = None) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE query_runs SET last_run_at=?,runs=runs+1,fetched=fetched+?,new_posts=new_posts+?,last_error=? WHERE query=?",
                               (datetime.now(timezone.utc).isoformat(), fetched, new_posts, error, query))
            if error is None and started_at is not None:
                connection.execute("UPDATE query_runs SET last_ok_at=? WHERE query=?", (started_at.isoformat(), query))
            connection.commit()

    def last_ok(self, query: str) -> datetime | None:
        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute("SELECT last_ok_at FROM query_runs WHERE query=?", (query,)).fetchone()
        try:
            return datetime.fromisoformat(row[0]) if row and row[0] else None
        except ValueError:
            return None


_TIME_OPERATOR = re.compile(r"(?:^|\s)(?:since|until|since_time|until_time|within_time):", re.I)


def time_window(query: str, last_ok: datetime | None, now: datetime | None = None) -> str:
    """Add an X date operator so each run only asks for posts newer than the previous successful run.

    First run of a query (e.g. a newly added product) looks back ``X_QUERY_FIRST_LOOKBACK_HOURS`` (default 7 days);
    later runs start at the previous run minus ``X_QUERY_OVERLAP_MINUTES``. ``X_QUERY_TIME_FILTER``:
    ``since_time`` (unix seconds, default), ``since`` (YYYY-MM-DD, coarser but official) or ``off``.
    Queries that already contain a date operator are left untouched."""
    mode = os.getenv("X_QUERY_TIME_FILTER", "since_time").strip().lower()
    if mode in {"", "off", "false", "0", "none"} or _TIME_OPERATOR.search(query):
        return query
    now = now or datetime.now(timezone.utc)
    lookback = timedelta(hours=float(os.getenv("X_QUERY_FIRST_LOOKBACK_HOURS", "168")))
    start = now - lookback if last_ok is None else max(now - lookback, last_ok - timedelta(minutes=float(os.getenv("X_QUERY_OVERLAP_MINUTES", "10"))))
    if mode == "since":
        return f"{query} since:{start.astimezone(timezone.utc).date().isoformat()}"
    return f"{query} since_time:{int(start.timestamp())}"


class XCollector:
    def __init__(self, client: Any | None = None, output_dir: str | Path | None = None, state_path: str | Path | None = None, max_per_query: int | None = None, daily_cap: int | None = None, sleep_min: float | None = None, sleep_max: float | None = None, max_retries: int | None = None, circuit_breaker: int | None = None, sleeper: Callable[[float], None] = time.sleep, jitter: Callable[[float, float], float] = random.uniform):
        self.client = client or XCliClient()
        self.output_dir = Path(output_dir or os.getenv("X_COLLECT_OUTPUT_DIR", "data/x_collected"))
        self.state_path = Path(state_path or os.getenv("X_COLLECT_STATE_FILE", "output/x_collector_state.sqlite3"))
        self.state: SeenStore | None = None
        self.max_per_query = max_per_query if max_per_query is not None else int(os.getenv("X_COLLECT_MAX_PER_QUERY", "50"))
        self.daily_cap = daily_cap if daily_cap is not None else int(os.getenv("X_COLLECT_DAILY_CAP", "500"))
        self.sleep_min = sleep_min if sleep_min is not None else float(os.getenv("X_COLLECT_SLEEP_MIN", "2"))
        self.sleep_max = sleep_max if sleep_max is not None else float(os.getenv("X_COLLECT_SLEEP_MAX", "5"))
        self.max_retries = max_retries if max_retries is not None else int(os.getenv("X_COLLECT_MAX_RETRIES", "3"))
        self.circuit_breaker = circuit_breaker if circuit_breaker is not None else int(os.getenv("X_COLLECT_CIRCUIT_BREAKER", "4"))
        self.sleeper, self.jitter = sleeper, jitter
        self.queries_per_cycle = int(os.getenv("X_COLLECT_QUERIES_PER_CYCLE", "12"))
        self.searches: list[dict[str, Any]] = []

    @staticmethod
    def load_queries(query_file: str | Path | None = None) -> list[str]:
        path = Path(query_file or os.getenv("X_COLLECT_QUERIES_FILE", "data/x_queries.txt"))
        if not path.is_file():
            return []
        lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
        return list(dict.fromkeys(line for line in lines if line and not line.startswith("#")))

    def run(self, queries: list[str | dict[str, Any]], dry_run: bool = False, mock_records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        normalized_queries = [dict(item) if isinstance(item, dict) else {"query": item, "kind": "product", "priority": 1.0} for item in queries]
        original_queries = [item["query"] for item in normalized_queries]
        invalid_queries = [query for query in original_queries if query.lstrip().startswith("-")]
        for query in invalid_queries:
            log.warning("Skipping query starting with '-': %s", query)
        normalized_queries = [item for item in normalized_queries if not item["query"].lstrip().startswith("-")]
        queries = [item["query"] for item in normalized_queries]
        today = date.today().isoformat()
        output_path = self.output_dir / f"{today}.jsonl"
        if dry_run:
            collected_today = 0
            if self.state_path.is_file():
                try:
                    with closing(sqlite3.connect(f"file:{self.state_path.resolve().as_posix()}?mode=ro", uri=True)) as connection:
                        row = connection.execute("SELECT collected FROM daily_counts WHERE day=?", (today,)).fetchone()
                        collected_today = int(row[0]) if row else 0
                except sqlite3.Error:
                    pass
            planned = sorted(normalized_queries, key=lambda item: (-(item.get("priority", 1) * {"intent": 1.0, "problem": 0.8, "product": 0.4}.get(item.get("kind"), 1.0)), item["query"]))[:self.queries_per_cycle]
            return {"status": "DRY_RUN", "queries": queries, "skipped_queries": invalid_queries, "limit_per_query": self.max_per_query, "daily_remaining": max(0, self.daily_cap - collected_today), "planned_queries": [{**item, "weight": item.get("priority", 1.0) * {"intent": 1.0, "problem": 0.8, "product": 0.4}.get(item.get("kind"), 1.0), "reason": "priority; stale history is considered on live runs"} for item in planned], "output": str(output_path), "cli_called": False}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state = SeenStore(self.state_path)
        if os.getenv("X_THREAD_COLLECT", "false").lower() in {"1", "true", "yes", "on"}:
            log.warning("X thread collection is enabled but the CLI thread contract is UNVERIFIED; no thread command is run by this worker yet")
        scheduled = self.state.schedule(normalized_queries, self.queries_per_cycle)
        queries = [item["query"] for item in scheduled]
        query_kinds = {item["query"]: item.get("kind", "product") for item in scheduled}
        query_products = {item["query"]: [str(x) for x in item.get("product_ids") or []] for item in scheduled}
        query_sellers = {item["query"]: [str(x) for x in item.get("business_ids") or []] for item in scheduled}
        self.searches = []   # successful searches of this run → per-seller search fee (charge_searches)
        collected, failures, failed_queries, consecutive_failures = [], 0, 0, 0
        stop_reason = None
        daily_remaining = max(0, self.daily_cap - self.state.count_today())
        cooldown = self.state.get_setting("rate_limit_until")
        if cooldown and datetime.now(timezone.utc) < datetime.fromisoformat(cooldown):
            result = {"status": "RATE_LIMITED", "collected": 0, "failures": 0, "output": str(output_path), "cooldown_until": cooldown}
            self._write_status(result)
            return result
        if daily_remaining == 0:
            result = {"status": "DAILY_CAP_REACHED", "collected": 0, "output": str(output_path)}
            self._write_status(result)
            return result
        latest_path = self.output_dir / "latest.jsonl"
        with output_path.open("a+", encoding="utf-8") as stream, latest_path.open("a", encoding="utf-8") as latest:
            stream.seek(0)
            already_written = set()
            for line in stream:
                try:
                    existing = json.loads(line)
                    if existing.get("id"):
                        already_written.add(str(existing["id"]) + (f"|{existing['product_id']}" if existing.get("product_id") else ""))
                except json.JSONDecodeError:
                    continue
            stream.seek(0, os.SEEK_END)
            for query_index, query in enumerate(queries):
                if len(collected) >= daily_remaining:
                    break
                retry = 0
                started_at = datetime.now(timezone.utc)
                search_query = time_window(query, self.state.last_ok(query), started_at)
                while retry <= self.max_retries:
                    try:
                        if mock_records is not None:
                            raw_items = mock_records
                        else:
                            raw_items = self.client.search(search_query, min(self.max_per_query, daily_remaining - len(collected)))
                        fetched_count = len(raw_items)
                        new_before = len(collected)
                        consecutive_failures = 0
                        records = []
                        for raw in raw_items[: self.max_per_query]:
                            base = normalize_tweet(raw, query=query)
                            if base is None:
                                continue
                            # per-product mode: one record per (post, product) — a post found for two sellers'
                            # products is analysed (and paid) for each of them separately
                            for pid in query_products.get(query) or [None]:
                                records.append(base if pid is None else {**base, "product_id": pid,
                                                                         "metadata": dict(base["metadata"])})
                        for normalized in records:
                            wkey = normalized["id"] + (f"|{normalized['product_id']}" if normalized.get("product_id") else "")
                            if wkey in already_written or self.state.has_seen(normalized):
                                continue
                            normalized["metadata"]["query_kind"] = query_kinds.get(query, "product")
                            if len(collected) >= daily_remaining:
                                break
                            write_offset = stream.tell()
                            line = json.dumps(normalized, ensure_ascii=False, default=str) + "\n"
                            stream.write(line)
                            stream.flush()
                            os.fsync(stream.fileno())
                            if not self.state.add(normalized, self.daily_cap):
                                stream.seek(write_offset)
                                stream.truncate()
                                continue
                            latest.write(line)
                            latest.flush()
                            os.fsync(latest.fileno())
                            already_written.add(wkey)
                            collected.append(normalized)
                        capped = len(collected) >= daily_remaining   # daily cap cut this search short: keep the old window
                        if mock_records is None and (query_products.get(query) or query_sellers.get(query)):
                            self.searches.append({"query": query, "product_ids": query_products.get(query) or [],
                                                  "business_ids": query_sellers.get(query) or []})
                        self.state.record_query(query, fetched_count, len(collected) - new_before, started_at=None if capped else started_at)
                        break
                    except XCliError as exc:
                        self.state.record_query(query, 0, 0, str(exc)[:300])
                        failures += 1
                        consecutive_failures += 1
                        if exc.kind == "rate_limit":
                            cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=RATE_LIMIT_COOLDOWN_SECONDS)
                            self.state.set_setting("rate_limit_until", cooldown_until.isoformat())
                            log.error("Rate limit received; collection paused until %s", cooldown_until.isoformat())
                            stop_reason = "RATE_LIMITED"
                            break
                        if exc.kind == "fatal":
                            log.error("Fatal CLI error; stopping collection: %s", exc)
                            stop_reason = "AUTH_FAILED" if "authentication" in str(exc).casefold() else "FAILED"
                            failed_queries += 1
                            break
                        if consecutive_failures >= self.circuit_breaker:
                            log.error("Stopping collection after consecutive CLI failures: %s", exc)
                            break
                        if exc.kind != "transient" or retry >= self.max_retries:
                            failed_queries += 1
                            log.warning("Query failed after retries: %s", exc)
                            break
                        delay = min(60.0, 2.0 ** retry) + self.jitter(0.0, 0.5)
                        self.sleeper(delay)
                        retry += 1
                if stop_reason or consecutive_failures >= self.circuit_breaker or query_index == len(queries) - 1:
                    break
                self.sleeper(self.jitter(self.sleep_min, self.sleep_max))
        status = stop_reason or ("RATE_LIMITED" if self.state.get_setting("rate_limit_until") and datetime.now(timezone.utc) < datetime.fromisoformat(self.state.get_setting("rate_limit_until")) else "CIRCUIT_OPEN" if consecutive_failures >= self.circuit_breaker else "PARTIAL" if failed_queries else "COMPLETED")
        result = {"status": status, "collected": len(collected), "failures": failures, "failed_queries": failed_queries, "output": str(output_path)}
        self._write_status(result)
        return result

    def _write_status(self, result: dict[str, Any]) -> None:
        target = Path(os.getenv("X_STATUS_FILE", str(self.output_dir / "status.json")))
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        payload = {key: result.get(key) for key in ("status", "collected", "failures", "failed_queries", "output", "cooldown_until")}
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["collected_today"] = self.state.count_today() if self.state else 0
        payload["daily_cap"] = self.daily_cap
        payload["last_cycle_at"] = payload["updated_at"]
        if self.state:
            with closing(sqlite3.connect(self.state_path)) as connection:
                payload["queries"] = [dict(zip(("query", "kind", "last_run_at", "fetched", "new_posts"), row))
                                          for row in connection.execute("SELECT query,kind,last_run_at,fetched,new_posts FROM query_runs ORDER BY query").fetchall()]
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)


def auto_ingest_enabled() -> bool:
    return os.getenv("X_AUTO_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}


def latest_offset(collector: XCollector) -> int:
    path = collector.output_dir / "latest.jsonl"
    return path.stat().st_size if path.exists() else 0


def ingest_new(collector: XCollector, offset: int, loader: Callable[[Path], int] | None = None) -> int:
    """Load only what this cycle appended to latest.jsonl into crawler.x_posts (the engine picks it up from there).
    Never raises: a DB hiccup must not stop collection; the posts stay in the JSONL files for a manual x_ingest."""
    path = collector.output_dir / "latest.jsonl"
    try:
        if not path.exists() or path.stat().st_size <= offset:
            return 0
        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read()
        chunk = chunk[:chunk.rfind(b"\n") + 1]   # whole lines only
        if not chunk.strip():
            return 0
        part = path.with_name(f".ingest-{os.getpid()}.jsonl")
        part.write_bytes(chunk)
        try:
            if loader is None:
                from x_ingest.__main__ import load as loader
            count = loader(part)
        finally:
            part.unlink(missing_ok=True)
        log.info("Auto-ingest: upserted %s X posts into the database", count)
        return count
    except Exception as exc:
        log.error("Auto-ingest failed (run `python -m x_ingest` later): %s", exc)
        return 0


def charge_searches(searches: list[dict[str, Any]], store: Any = None) -> int:
    """Per-seller fee for each search made for their products (X_SEARCH_FEE_TOMAN, default 0 = free).
    Every seller whose product shares the query pays the whole fee. Never raises."""
    fee = float(os.getenv("X_SEARCH_FEE_TOMAN", "0") or 0)
    if fee <= 0 or not searches:
        return 0
    try:
        from need_engine.config import EngineConfig

        cfg = EngineConfig()
        own = store is None
        if own:
            from need_engine.store import Store

            store = Store(cfg.database_url, cfg.state_schema)
        rows = 0
        try:
            for search in searches:
                sellers = sorted(set(search.get("business_ids") or []))
                if not sellers:
                    continue
                usd = fee / max(1.0, float(cfg.usd_to_toman))
                pids = ",".join(search.get("product_ids") or [])
                store.add_cost("x_search", "x", 0, 0, False, usd * len(sellers), fee * len(sellers), ref=f"xq:{pids}",
                               businesses=sellers)
                rows += len(sellers)
        finally:
            if own:
                store.close()
        return rows
    except Exception as exc:
        log.error("X search fee not recorded: %s", exc)
        return 0


def collect_forever(collector: XCollector, queries: list[str] | Callable[[], list[str]], interval: float, mock_records: list[dict[str, Any]] | None = None) -> None:
    while True:
        current_queries = queries() if callable(queries) else queries
        offset = latest_offset(collector)
        result = collector.run(current_queries, mock_records=mock_records)
        log.info("Collection cycle finished: status=%s collected=%s", result["status"], result.get("collected", 0))
        if mock_records is None and auto_ingest_enabled():
            ingest_new(collector, offset)
        if mock_records is None:
            charge_searches(getattr(collector, "searches", []))
        if result["status"] in {"CIRCUIT_OPEN", "RATE_LIMITED", "AUTH_FAILED", "FAILED"}:
            raise XCliError(f"Collector stopped with status {result['status']}")
        time.sleep(interval)
