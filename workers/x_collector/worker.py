from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import random
import sqlite3
import time
from contextlib import closing, nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import cli_mapping as mapping
from .client import XCliClient, XCliError
from .intent_queries import KIND_WEIGHTS
from file_lock import exclusive_file_lock
from shared.collector_kit.heartbeat import beat_error, beat_ok
from shared.collector_kit.seen import SeenStore
from shared.collector_kit.state_store import create_state_store
from shared.records import SourceRecord, to_json_line
from shared.x_filters import is_promotional_post, is_political_x_post
from shared.collector_kit.jsonl import append_line
from shared.collector_kit.limits import CircuitBreaker, Cooldown, DailyCap, retry_delay
from shared.collector_kit.status import write_status

log = logging.getLogger("x_collector")
RATE_LIMIT_COOLDOWN_SECONDS = 15 * 60


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
    if is_political_x_post(text):
        log.info("Skipping political X post %s (filtered by embedding with high confidence)", tweet_id)
        return None
    clean_id = str(tweet_id).strip()
    if not clean_id:
        return None
    if handle:
        handle = str(handle).strip().lstrip("@").split("/")[0]
    tweet_url = f"https://x.com/{handle}/status/{clean_id}" if handle else None
    created_at = _normalize_created_at(_first(raw, mapping.OUTPUT_CREATED_KEYS))
    if created_at:
        try:
            created_dt = datetime.fromisoformat(created_at)
            if created_dt < datetime.now(timezone.utc) - timedelta(days=10):
                log.info("Skipping X post %s older than 10 days (%s)", clean_id, created_at)
                return None
        except Exception:
            pass
    return SourceRecord.from_dict({
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
    }).as_dict()


def compute_tweet_hash(tweet_id: str | int) -> str:
    clean_id = str(tweet_id).strip()
    return hashlib.sha256(f"x_tweet:{clean_id}".encode("utf-8")).hexdigest()


def filter_uncrawled_tweets(records: list[dict[str, Any]], dsn: str | None = None) -> list[dict[str, Any]]:
    if not records:
        return records
    try:
        from x_ingest.__main__ import _dsn
        import psycopg
        hashes = {compute_tweet_hash(r["id"]): r for r in records if r.get("id")}
        if not hashes:
            return records
        with psycopg.connect(dsn or _dsn(), connect_timeout=3) as conn:
            has_table = conn.execute("SELECT to_regclass('discovery_xcrawledpost')").fetchone()[0]
            if not has_table:
                return records
            rows = conn.execute(
                "SELECT post_hash FROM discovery_xcrawledpost WHERE post_hash = ANY(%s)",
                (list(hashes.keys()),)
            ).fetchall()
            seen_hashes = {r[0] for r in rows}
            if seen_hashes:
                log.info("Skipping %d duplicate tweet(s) already recorded in XCrawledPost", len(seen_hashes))
            return [r for r in records if compute_tweet_hash(r["id"]) not in seen_hashes]
    except Exception as exc:
        log.debug("Database hash dedup check skipped: %s", exc)
        return records



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


def _within_output_root(path: Path, root: Path) -> Path:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    try:
        resolved_path.relative_to(resolved_root)
        return resolved_path
    except ValueError:
        return resolved_root / path.name


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
    max_lookback = timedelta(days=10)
    requested_hours = float(os.getenv("X_QUERY_FIRST_LOOKBACK_HOURS", "240"))
    lookback = min(timedelta(hours=requested_hours), max_lookback)
    start = now - lookback if last_ok is None else max(now - lookback, last_ok - timedelta(minutes=float(os.getenv("X_QUERY_OVERLAP_MINUTES", "10"))))
    start = max(start, now - max_lookback)
    if mode == "since":
        return f"{query} since:{start.astimezone(timezone.utc).date().isoformat()}"
    return f"{query} since_time:{int(start.timestamp())}"


class XCollector:
    def __init__(self, client: Any | None = None, output_dir: str | Path | None = None, state_path: str | Path | None = None, max_per_query: int | None = None, daily_cap: int | None = None, sleep_min: float | None = None, sleep_max: float | None = None, max_retries: int | None = None, circuit_breaker: int | None = None, sleeper: Callable[[float], None] = time.sleep, jitter: Callable[[float, float], float] = random.uniform):
        self.client = client or XCliClient()
        raw_output_dir = Path(output_dir or os.getenv("X_COLLECT_OUTPUT_DIR", "data/x_collected"))
        project_root = Path(__file__).resolve().parents[2]
        resolved_output = raw_output_dir if raw_output_dir.is_absolute() else project_root / raw_output_dir
        self.output_dir = resolved_output.resolve()
        raw_state_path = Path(state_path or os.getenv("X_COLLECT_STATE_FILE", "output/x_collector_state.sqlite3"))
        resolved_state_path = raw_state_path if raw_state_path.is_absolute() else project_root / raw_state_path
        self.state_path = resolved_state_path.resolve()
        self.state_backend = os.getenv("COLLECTOR_STATE_BACKEND", "sqlite").strip().lower()
        self.state: Any = None
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

    def run(self, queries: list[str | dict[str, Any]], dry_run: bool = False, mock_records: list[dict[str, Any]] | None = None, *, _retention_locked: bool = False) -> dict[str, Any]:
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
            if self.state_backend == "sqlite" and self.state_path.is_file():
                try:
                    dry_state = SeenStore(self.state_path, platform="x")
                    collected_today = dry_state.count_today()
                except (sqlite3.Error, OSError):
                    pass
            planned = sorted(normalized_queries, key=lambda item: (-(item.get("priority", 1) * KIND_WEIGHTS.get(item.get("kind"), 1.0)), item["query"]))[:self.queries_per_cycle]
            return {"status": "DRY_RUN", "queries": queries, "skipped_queries": invalid_queries, "limit_per_query": self.max_per_query, "daily_remaining": max(0, self.daily_cap - collected_today), "planned_queries": [{**item, "weight": item.get("priority", 1.0) * KIND_WEIGHTS.get(item.get("kind"), 1.0), "reason": "priority; stale history is considered on live runs"} for item in planned], "output": str(output_path), "cli_called": False}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        guard = nullcontext() if _retention_locked else exclusive_file_lock(self.output_dir / ".retention_guard.lock")
        try:
            with guard:
                state_lock = (self.state_path.with_suffix(self.state_path.suffix + ".lock")
                              if self.state_backend == "sqlite" else self.output_dir / ".collector_state.lock")
                with exclusive_file_lock(state_lock):
                    self.state = create_state_store(self.state_path, platform="x", sqlite_factory=SeenStore)
                    result = self._run_locked(normalized_queries, output_path, mock_records)
            if result.get("status") in {"COMPLETED", "DAILY_CAP_REACHED"} and not result.get("failures"):
                beat_ok("x", int(result.get("collected", 0)))
            else:
                beat_error("x", f"cycle status {result.get('status', 'unknown')}",
                           result.get("cooldown_until"))
            return result
        except Exception as exc:
            beat_error("x", type(exc).__name__)
            raise

    def _run_locked(self, normalized_queries: list[dict[str, Any]], output_path: Path,
                    mock_records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if os.getenv("X_THREAD_COLLECT", "false").lower() in {"1", "true", "yes", "on"}:
            log.warning("X thread collection is enabled but the CLI thread contract is UNVERIFIED; no thread command is run by this worker yet")
        scheduled = self.state.schedule(normalized_queries, self.queries_per_cycle)
        queries = [item["query"] for item in scheduled]
        query_kinds = {item["query"]: item.get("kind", "product") for item in scheduled}
        query_products = {item["query"]: [str(x) for x in item.get("product_ids") or []] for item in scheduled}
        query_sellers = {item["query"]: [str(x) for x in item.get("business_ids") or []] for item in scheduled}
        self.searches = []
        collected, failures, failed_queries = [], 0, 0
        breaker = CircuitBreaker(self.circuit_breaker)
        stop_reason = None
        daily_remaining = DailyCap(self.daily_cap, self.state.count_today()).remaining
        cooldown = Cooldown(self.state, RATE_LIMIT_COOLDOWN_SECONDS)
        cooldown_until = cooldown.active_until()
        if cooldown_until:
            result = {"status": "RATE_LIMITED", "collected": 0, "failures": 0,
                      "output": str(output_path), "cooldown_until": cooldown_until}
            self._write_status(result)
            return result
        if daily_remaining == 0:
            result = {"status": "DAILY_CAP_REACHED", "collected": 0, "output": str(output_path)}
            self._write_status(result)
            return result

        latest_path = self.output_dir / "latest.jsonl"
        with exclusive_file_lock(latest_path.with_suffix(latest_path.suffix + ".lock")):
            already_written = self._written_keys(output_path)
            for query_index, query in enumerate(queries):
                if len(collected) >= daily_remaining:
                    break
                retry = 0
                started_at = datetime.now(timezone.utc)
                search_query = time_window(query, self.state.last_ok(query), started_at)
                while retry <= self.max_retries:
                    try:
                        raw_items = mock_records if mock_records is not None else self.client.search(
                            search_query, min(self.max_per_query, daily_remaining - len(collected)))
                        fetched_count = len(raw_items)
                        new_before = len(collected)
                        breaker.success()
                        records = self._records_for_query(raw_items, query, query_products)
                        records = filter_uncrawled_tweets(records)
                        for record in records:
                            key = record["id"] + (f"|{record['product_id']}" if record.get("product_id") else "")
                            if key in already_written or self.state.has_seen(record):
                                continue
                            record["metadata"]["query_kind"] = query_kinds.get(query, "product")
                            if len(collected) >= daily_remaining:
                                break
                            line = to_json_line(SourceRecord.from_dict(record))
                            output_offset = append_line(output_path, line)
                            latest_offset = append_line(latest_path, line, locked=True)
                            if not self.state.add(record, self.daily_cap):
                                self._truncate(output_path, output_offset)
                                self._truncate(latest_path, latest_offset)
                                continue
                            already_written.add(key)
                            collected.append(record)
                        self._record_search(query, query_products, query_sellers, mock_records,
                                            raw_items, collected, new_before, started_at)
                        capped = len(collected) >= daily_remaining
                        self.state.record_query(query, fetched_count, len(collected) - new_before,
                                                started_at=None if capped else started_at)
                        break
                    except XCliError as exc:
                        self.state.record_query(query, 0, 0, str(exc)[:300])
                        failures += 1
                        circuit_open = breaker.failure()
                        if exc.kind == "rate_limit":
                            cooldown_until = cooldown.start()
                            log.error("Rate limit received; collection paused until %s", cooldown_until)
                            stop_reason = "RATE_LIMITED"
                            break
                        if exc.kind == "fatal":
                            log.error("Fatal CLI error; stopping collection: %s", exc)
                            stop_reason = "AUTH_FAILED" if "authentication" in str(exc).casefold() else "FAILED"
                            failed_queries += 1
                            break
                        if circuit_open:
                            log.error("Stopping collection after consecutive CLI failures: %s", exc)
                            break
                        if exc.kind != "transient" or retry >= self.max_retries:
                            failed_queries += 1
                            log.warning("Query failed after retries: %s", exc)
                            break
                        self.sleeper(retry_delay(retry, self.jitter))
                        retry += 1
                if stop_reason or breaker.failures >= self.circuit_breaker or query_index == len(queries) - 1:
                    break
                self.sleeper(self.jitter(self.sleep_min, self.sleep_max))
        status = stop_reason or ("RATE_LIMITED" if cooldown.active_until() else "CIRCUIT_OPEN" if breaker.failures >= self.circuit_breaker else "PARTIAL" if failed_queries else "COMPLETED")
        result = {"status": status, "collected": len(collected), "failures": failures,
                  "failed_queries": failed_queries, "output": str(output_path)}
        self._write_status(result)
        return result

    @staticmethod
    def _written_keys(path: Path) -> set[str]:
        keys = set()
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("id"):
                        keys.add(str(row["id"]) + (f"|{row['product_id']}" if row.get("product_id") else ""))
        return keys

    def _records_for_query(self, raw_items, query: str, query_products: dict[str, list[str]]) -> list[dict[str, Any]]:
        records = []
        for raw in raw_items[:self.max_per_query]:
            base = normalize_tweet(raw, query=query)
            if base is None:
                continue
            for product_id in query_products.get(query) or [None]:
                records.append(base if product_id is None else {
                    **base, "product_id": product_id, "metadata": dict(base["metadata"]),
                })
        return records

    @staticmethod
    def _truncate(path: Path, offset: int) -> None:
        with path.open("r+b") as stream:
            stream.truncate(offset)

    def _record_search(self, query, query_products, query_sellers, mock_records, raw_items,
                       collected, new_before, started_at) -> None:
        if mock_records is not None or not (query_products.get(query) or query_sellers.get(query)):
            return
        new_by_product: dict[str, int] = {}
        for record in collected[new_before:]:
            if record.get("product_id"):
                product_id = record["product_id"]
                new_by_product[product_id] = new_by_product.get(product_id, 0) + 1
        self.searches.append({"query": query, "product_ids": query_products.get(query) or [],
                              "business_ids": query_sellers.get(query) or [], "fetched": len(raw_items),
                              "new_by_product": new_by_product, "ran_at": started_at.isoformat()})

    def _write_status(self, result: dict[str, Any]) -> None:
        target = _within_output_root(Path(os.getenv("X_STATUS_FILE", str(self.output_dir / "status.json"))), self.output_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        payload = {key: result.get(key) for key in ("status", "collected", "failures", "failed_queries", "output", "cooldown_until")}
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["collected_today"] = self.state.count_today() if self.state else 0
        payload["daily_cap"] = self.daily_cap
        payload["last_cycle_at"] = payload["updated_at"]
        if self.state:
            payload["queries"] = self.state.query_status()
        write_status(target, payload)


def auto_ingest_enabled() -> bool:
    return os.getenv("X_AUTO_INGEST", "true").strip().lower() in {"1", "true", "yes", "on"}


def latest_offset(collector: XCollector) -> int:
    path = collector.output_dir / "latest.jsonl"
    return path.stat().st_size if path.exists() else 0


def ingest_new(collector: XCollector, offset: int, loader: Callable[[Path], int] | None = None,
               *, _retention_locked: bool = False) -> int:
    """Load only what this cycle appended to latest.jsonl into crawler.x_posts (the engine picks it up from there).
    Never raises: a DB hiccup must not stop collection; the posts stay in the JSONL files for a manual x_ingest."""
    path = collector.output_dir / "latest.jsonl"
    try:
        # Share the retention lock and the latest-file lock. This prevents cleanup from
        # scrubbing a segment after it was copied but before x_ingest reads it.
        guard = nullcontext() if _retention_locked else exclusive_file_lock(collector.output_dir / ".retention_guard.lock")
        with guard:
            with exclusive_file_lock(path.with_suffix(path.suffix + ".lock")):
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


def record_searches(searches: list[dict[str, Any]], dsn: str | None = None) -> int:
    """Seller activity log: each search made for a product (when, phrase, posts seen, new posts) → crawler.x_search_runs.
    Never raises."""
    if not searches:
        return 0
    try:
        from x_ingest.__main__ import record_search_runs

        return record_search_runs(searches, dsn)
    except Exception as exc:
        log.error("X search log not recorded: %s", exc)
        return 0


def collect_forever(collector: XCollector, queries: list[str] | Callable[[], list[str]], interval: float, mock_records: list[dict[str, Any]] | None = None) -> None:
    while True:
        current_queries = queries() if callable(queries) else queries
        guard = collector.output_dir / ".retention_guard.lock"
        with exclusive_file_lock(guard):
            offset = latest_offset(collector)
            result = collector.run(current_queries, mock_records=mock_records, _retention_locked=True)
            log.info("Collection cycle finished: status=%s collected=%s", result["status"], result.get("collected", 0))
            if mock_records is None and auto_ingest_enabled():
                ingest_new(collector, offset, _retention_locked=True)
            if mock_records is None:
                charge_searches(getattr(collector, "searches", []))
                record_searches(getattr(collector, "searches", []))
                try:
                    from workers.x_collector.proqe import sync_bandit_feedback
                    if collector.state:
                        sync_bandit_feedback(collector.state)
                except Exception as exc:
                    log.error("Bandit feedback sync failed: %s", exc)
        if result["status"] == "RATE_LIMITED":
            log.warning("X rate limited; sleeping for 15 minutes before retry...")
            time.sleep(900)
            continue
        elif result["status"] == "CIRCUIT_OPEN":
            log.warning("X circuit open; sleeping for 5 minutes before retry...")
            time.sleep(300)
            continue
        elif result["status"] == "FAILED":
            log.warning("X collector cycle failed; sleeping for 2 minutes before retry...")
            time.sleep(120)
            continue
        elif result["status"] == "AUTH_FAILED":
            log.error("X authentication failed; sleeping 1 hour before recheck...")
            time.sleep(3600)
            continue
        time.sleep(interval)

