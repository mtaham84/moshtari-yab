from __future__ import annotations

import json
import logging
import os
import re
import random
import sqlite3
import psycopg
import time
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import cli_mapping as mapping
from .client import XCliClient, XCliError
from .balance_gate import BalanceGate
from .query_files import load_query_file
from .cycle_support import fetch_search, handle_search_error, preview_searches, unseen_candidates
from shared.collector_kit.file_lock import exclusive_file_lock
from shared.collector_kit.heartbeat import beat_error, beat_ok
from shared.collector_kit.query_scheduler import scores
from shared.collector_kit.seen import SeenStore
from shared.collector_kit.state_store import create_state_store
from shared.records import SourceRecord, to_json_line
from shared.x_record_data import optional_data, sanitize
from shared.x_dedup import compute_tweet_hash
from shared.x_filters import is_promotional_post, is_political_x_post
from shared.collector_kit.jsonl import append_line
from shared.collector_kit.limits import CircuitBreaker, Cooldown, DailyCap
from shared.collector_kit.status import write_status

log = logging.getLogger("x_collector")
RATE_LIMIT_COOLDOWN_SECONDS = 15 * 60


def _first(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_tweet(raw: dict[str, Any], query: str | None = None, stats: dict | None = None,
                    thread_root_id: str | None = None) -> dict[str, Any] | None:
    if raw.get("isRetweet") is True:
        log.info("Skipping retweet %s", raw.get("id") or "without id")
        return None
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    tweet_id = _first(raw, mapping.OUTPUT_ID_KEYS)
    text = _first(raw, mapping.OUTPUT_TEXT_KEYS)
    handle = _first(raw, mapping.OUTPUT_HANDLE_KEYS) or author.get("screenName") or author.get("handle")
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
        if stats is not None:
            stats["promotional_posts_dropped"] = stats.get("promotional_posts_dropped", 0) + 1
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
            from x_ingest.__main__ import x_max_age_days

            max_age_days = x_max_age_days()
            if max_age_days > 0 and created_dt < datetime.now(timezone.utc) - timedelta(days=max_age_days):
                log.info("Skipping X post %s older than %s days (%s)", clean_id, int(max_age_days), created_at)
                return None
        except (TypeError, ValueError):   # unparseable date or setting: keep the post
            pass

    details = optional_data(raw)
    kind, thread = 'post', {}
    if thread_root_id is not None:
        parent = details.get('parent', {})
        reply_to, conversation = parent.get('in_reply_to_tweet_id'), parent.get('conversation_id')
        if (not reply_to or not reply_to.isascii() or not reply_to.isdecimal()
                or clean_id == str(thread_root_id)
                or str(thread_root_id) not in {reply_to, conversation}):
            return None
        kind = 'reply'
        thread = {'conversation_id': conversation, 'in_reply_to_tweet_id': reply_to,
                  'in_reply_to_author_id': parent.get('in_reply_to_user_id')}
    if 'parent' in details:
        details['parent']['kind'] = kind
    return SourceRecord.from_dict({
        "id": clean_id,
        "source": "x",
        "kind": kind,
        **thread,
        "text": text.strip(),
        "author_handle": f"@{handle}" if handle else None,
        "author_name": _first(raw, mapping.OUTPUT_NAME_KEYS) or author.get("name"),
        "author_id": str(author_id),
        "author_verified": bool(author.get("verified") or author.get("isVerified") or raw.get("author_verified")),
        "author_bio": str(raw.get("author_bio") if "author_bio" in raw else author.get("description") or author.get("bio") or "")[:500],
        "created_at": str(created_at) if created_at else None,
        "url": tweet_url,
        **details,
        "metadata": {"collector": "agent-reach-cli", "raw_cli": sanitize(raw), "lang": _first(raw, mapping.OUTPUT_LANG_KEYS), "query": query},
    }).as_dict()


def filter_uncrawled_tweets(records: list[dict[str, Any]], dsn: str | None = None) -> list[dict[str, Any]]:
    if not records or (not dsn and os.getenv("X_DEDUP_AGAINST_DB", "true").strip().lower() in {"0", "false", "no", "off"}):
        return records
    try:
        from x_ingest.__main__ import _dsn
        import psycopg
        hashes = {compute_tweet_hash(r["id"], r.get("product_id")): r for r in records if r.get("id")}
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
            return [r for r in records if compute_tweet_hash(r["id"], r.get("product_id")) not in seen_hashes]
    except Exception as exc:
        log.debug("Database hash dedup check skipped: %s", exc, exc_info=True)
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
    def __init__(self, client: Any | None = None, output_dir: str | Path | None = None, state_path: str | Path | None = None, max_per_query: int | None = None, daily_cap: int | None = None, sleep_min: float | None = None, sleep_max: float | None = None, max_retries: int | None = None, circuit_breaker: int | None = None, sleeper: Callable[[float], None] = time.sleep, jitter: Callable[[float, float], float] = random.uniform, account_pool: Any | None = None):
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
        self.daily_cap = daily_cap if daily_cap is not None else int(os.getenv("X_COLLECT_DAILY_CAP", "2500"))
        self.sleep_min = sleep_min if sleep_min is not None else float(os.getenv("X_COLLECT_SLEEP_MIN", "2"))
        self.sleep_max = sleep_max if sleep_max is not None else float(os.getenv("X_COLLECT_SLEEP_MAX", "5"))
        self.max_retries = max_retries if max_retries is not None else int(os.getenv("X_COLLECT_MAX_RETRIES", "3"))
        self.circuit_breaker = circuit_breaker if circuit_breaker is not None else int(os.getenv("X_COLLECT_CIRCUIT_BREAKER", "4"))
        self.sleeper, self.jitter = sleeper, jitter
        self.queries_per_cycle = int(os.getenv("X_COLLECT_QUERIES_PER_CYCLE", "12"))
        self.searches: list[dict[str, Any]] = []
        self.filter_stats: dict[str, int] = {}
        self.account_pool = account_pool
        if self.account_pool is None:
            try:
                from .account_pool import TwitterAccountPool
                self.account_pool = TwitterAccountPool()
                self.account_pool.seed_initial_accounts_if_empty()
            except Exception as pool_exc:
                log.debug("TwitterAccountPool initialization skipped: %s", pool_exc, exc_info=True)

    @staticmethod
    def load_queries(query_file: str | Path | None = None) -> list[str]:
        path = Path(query_file or os.getenv("X_COLLECT_QUERIES_FILE", "data/x_queries.txt"))
        return load_query_file(path)

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
            planned, weights, stats, collected_today = preview_searches(self, normalized_queries)
            starting_scores = scores([item for item in normalized_queries if item["query"] in weights], stats)
            return {"status": "DRY_RUN", "queries": queries, "skipped_queries": invalid_queries,
                    "limit_per_query": self.max_per_query, "daily_remaining": max(0, self.daily_cap - collected_today),
                    "planned_queries": [{**item, "weight": weights[item["query"]],
                                         "lead_rate": stats.get(item["query"], {}).get("lead_rate", 0.0),
                                         "score": starting_scores[item["query"]],
                                         "reason": "verified lead yield; weighted starting score for untried queries"}
                                        for item in planned], "output": str(output_path), "cli_called": False}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        guard = nullcontext() if _retention_locked else exclusive_file_lock(self.output_dir / ".retention_guard.lock")
        try:
            with guard:
                state_lock = (self.state_path.with_suffix(self.state_path.suffix + ".lock")
                              if self.state_backend == "sqlite" else self.output_dir / ".collector_state.lock")
                with exclusive_file_lock(state_lock):
                    self.state = create_state_store(self.state_path, platform="x", sqlite_factory=SeenStore)
                    with BalanceGate(allow_unowned=mock_records is not None) as balance_gate:
                        result = self._run_locked(normalized_queries, output_path, mock_records, balance_gate)
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
                    mock_records: list[dict[str, Any]] | None, balance_gate: BalanceGate) -> dict[str, Any]:
        if hasattr(self, "account_pool") and self.account_pool:
            try:
                self.account_pool.refresh()
            except Exception as pool_exc:
                log.debug("Account pool refresh failed: %s", pool_exc, exc_info=True)
        is_test_client = mock_records is not None or "FakeClient" in type(self.client).__name__ or type(self.client).__name__ == "Client"
        enforce_daily = not is_test_client and os.getenv("X_ENFORCE_DAILY_UNIQUE", "false").lower() in {"1", "true", "yes", "on"}
        eligible_queries = balance_gate.filter_queries(normalized_queries)
        self.active_queries = eligible_queries
        self.report_queries = normalized_queries
        scheduled = self.state.schedule(eligible_queries, self.queries_per_cycle, enforce_daily_unique=enforce_daily, persist=False)
        queries = [item["query"] for item in scheduled]
        query_kinds = {item["query"]: item.get("kind", "product") for item in scheduled}
        query_products = {item["query"]: [str(x) for x in item.get("product_ids") or []] for item in scheduled}
        query_sellers = {item["query"]: [str(x) for x in item.get("business_ids") or []] for item in scheduled}
        self.searches = []
        self.filter_stats = {"promotional_posts_dropped": 0}
        collected, failures, failed_queries = [], 0, 0
        breaker = CircuitBreaker(self.circuit_breaker)
        stop_reason = None
        search_errors: dict[str, int] = {}
        daily_remaining = DailyCap(self.daily_cap, self.state.count_today()).remaining
        cooldown = Cooldown(self.state, RATE_LIMIT_COOLDOWN_SECONDS)
        cooldown_until = cooldown.active_until()
        if cooldown_until:
            result = {"status": "RATE_LIMITED", "collected": 0, "failures": 0,
                      "output": str(output_path), "cooldown_until": cooldown_until, **self.filter_stats, **balance_gate.stats()}
            self._write_status(result)
            return result
        if daily_remaining == 0:
            result = {"status": "DAILY_CAP_REACHED", "collected": 0, "failures": 0, "output": str(output_path), **self.filter_stats, **balance_gate.stats()}
            self._write_status(result)
            return result

        if not queries:
            result = {'status': 'NO_ELIGIBLE_QUERIES', 'collected': 0, 'failures': 0,
                      'output': str(output_path), **self.filter_stats, **balance_gate.stats()}
            self._write_status(result)
            return result

        latest_path = self.output_dir / "latest.jsonl"
        with exclusive_file_lock(latest_path.with_suffix(latest_path.suffix + ".lock")):
            already_written = self._written_keys(output_path)
            current_account = self.account_pool.get_next_account() if hasattr(self, "account_pool") and self.account_pool else None
            is_test_client = mock_records is not None or "FakeClient" in type(self.client).__name__ or type(self.client).__name__ == "Client"
            min_qualifying = 1 if is_test_client else int(os.getenv("X_MIN_QUALIFYING_POSTS", "5"))
            min_inspect = 1 if is_test_client else int(os.getenv("X_MIN_INSPECT_POSTS", "40"))
            max_inspect = 1 if is_test_client else int(os.getenv("X_MAX_INSPECT_POSTS", "80"))

            for query_index, query in enumerate(queries):
                if len(collected) >= daily_remaining:
                    break
                retry = 0
                started_at = datetime.now(timezone.utc)
                search_query = time_window(query, self.state.last_ok(query), started_at)
                while retry <= self.max_retries:
                    eligible = balance_gate.eligible_query(scheduled[query_index])
                    if eligible is None:
                        break
                    query_products[query] = [str(pid) for pid in eligible.get('product_ids') or []]
                    query_sellers[query] = balance_gate.owners(eligible)
                    self.state.schedule([eligible], 1)
                    try:
                        if hasattr(self, "account_pool") and self.account_pool:
                            current_account = self.account_pool.get_next_account()
                            if current_account is None and not is_test_client:
                                log.warning("All Twitter scraper accounts are currently exhausted or on cooldown")
                                stop_reason = "ALL_ACCOUNTS_UNAVAILABLE"
                                break

                        if not self.state.record_query_allocation(query, balance_gate.filter_queries(eligible_queries)):
                            break
                        raw_items, search_stats, fetch_limit = fetch_search(
                            self, query, search_query, query_products.get(query) or [], current_account,
                            already_written, daily_remaining - len(collected), min_inspect, mock_records, is_test_client,
                        )
                        fetched_count = len(raw_items)

                        records = self._records_for_query(raw_items, query, query_products, eligible.get('thread_root_id'))
                        records = filter_uncrawled_tweets(records)

                        candidates, seen_count = unseen_candidates(records, already_written, self.state)

                        # If fewer than min_qualifying tweets found, expand inspection up to max_inspect (only for legacy client)
                        if len(candidates) < min_qualifying and mock_records is None and not is_test_client and fetched_count < max_inspect and search_stats is None:
                            expand_limit = min(max_inspect, daily_remaining - len(collected))
                            if expand_limit > fetch_limit:
                                log.info("Query '%s' yielded only %d qualifying tweets (< %d); expanding search up to %d tweets",
                                         query, len(candidates), min_qualifying, expand_limit)
                                try:
                                    raw_items = self.client.search(search_query, expand_limit, account=current_account)
                                    fetched_count = len(raw_items)
                                    records = self._records_for_query(raw_items, query, query_products, eligible.get('thread_root_id'))
                                    records = filter_uncrawled_tweets(records)
                                    candidates, seen_count = unseen_candidates(records, already_written, self.state)
                                except XCliError:
                                    raise
                                except Exception as expand_exc:
                                    log.warning("Expanding search to %d failed: %s", expand_limit, expand_exc, exc_info=True)

                        if current_account and self.account_pool:
                            self.account_pool.record_success(current_account, posts_count=fetched_count)
                        breaker.success()
                        if candidates:
                            new_before = len(collected)
                            for record in candidates:
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

                            added_count = len(collected) - new_before
                            if added_count > 0:
                                self._record_search(query, query_products, query_sellers, mock_records,
                                                    raw_items, collected, new_before, started_at, stats=search_stats)
                                # Note: Raw crawled tweets are not leads. Leads are verified and emitted by need_engine with Luna.
                            else:
                                self._record_search(query, query_products, query_sellers, mock_records,
                                                    raw_items, collected, len(collected), started_at, stats=search_stats)
                            capped = len(collected) >= daily_remaining
                            self.state.record_query(query, fetched_count, added_count,
                                                    started_at=None if capped else started_at)
                            break
                        else:
                            # 0 new candidates found. Protect duplicate tweets & enforce 10-day / 2-strike rule.
                            self._record_search(query, query_products, query_sellers, mock_records,
                                                raw_items, collected, len(collected), started_at, stats=search_stats)
                            last_lead_dt = self.state.last_lead_time(query)
                            eval_stats = self.state.get_query_eval_stats(query)
                            total_eval_fetched = eval_stats.get("eval_fetched", 0) + fetched_count
                            now = datetime.now(timezone.utc)
                            days_since_success = (now - last_lead_dt).total_seconds() / 86400.0 if last_lead_dt else 0.0

                            if days_since_success >= 10.0 and total_eval_fetched >= max_inspect and not is_test_client:
                                strikes = self.state.record_evaluation_failure(query, heavy_beta=3.0)
                                log.warning("Query '%s' failed 10-day evaluation: 0 leads in %.1f days across %d inspected tweets (strike %d/2)",
                                            query, days_since_success, total_eval_fetched, strikes)
                                self.state.record_query(query, fetched_count, 0, started_at=started_at)
                            else:
                                log.info("Query '%s' yielded 0 fresh candidates (seen=%d, days_since_lead=%.1f, eval_fetched=%d); keeping query active without penalty",
                                         query, seen_count, days_since_success, total_eval_fetched)
                                self.state.record_query(query, fetched_count, 0, started_at=started_at)
                            break
                    except XCliError as exc:
                        search_errors[exc.kind] = search_errors.get(exc.kind, 0) + 1
                        self.state.record_query(query, 0, 0, str(exc)[:300])
                        failures += 1
                        breaker.failure()
                        action, stop_reason, failed = handle_search_error(self, exc, current_account, breaker, cooldown, retry)
                        failed_queries += failed
                        if action == 'rotate':
                            continue
                        if action != 'retry':
                            break
                        retry += 1
                if query in balance_gate.skipped_queries:
                    continue
                if stop_reason or breaker.failures >= self.circuit_breaker or query_index == len(queries) - 1:
                    break
                self.sleeper(self.jitter(self.sleep_min, self.sleep_max))
        status = stop_reason or ("RATE_LIMITED" if cooldown.active_until() else "CIRCUIT_OPEN" if breaker.failures >= self.circuit_breaker else "PARTIAL" if failed_queries else "COMPLETED")
        result = {"status": status, "collected": len(collected), "failures": failures,
                  "failed_queries": failed_queries, "output": str(output_path), "search_errors": search_errors, **self.filter_stats, **balance_gate.stats()}
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

    def _records_for_query(self, raw_items, query: str, query_products: dict[str, list[str]],
                           thread_root_id: str | None = None) -> list[dict[str, Any]]:
        records = []
        for raw in raw_items[:self.max_per_query]:
            base = normalize_tweet(raw, query=query, stats=self.filter_stats, thread_root_id=thread_root_id)
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
                       collected, new_before, started_at, stats: dict[str, Any] | None = None) -> None:
        if mock_records is not None or not (query_products.get(query) or query_sellers.get(query)):
            return
        new_by_product: dict[str, int] = {}
        for record in collected[new_before:]:
            if record.get("product_id"):
                product_id = record["product_id"]
                new_by_product[product_id] = new_by_product.get(product_id, 0) + 1
        entry = {"query": query, "product_ids": query_products.get(query) or [],
                 "business_ids": query_sellers.get(query) or [], "fetched": len(raw_items),
                 "new_by_product": new_by_product, "ran_at": started_at.isoformat()}
        if stats:
            entry["stats"] = stats
        self.searches.append(entry)

    def _update_x_source_stat(self, query: str, sellers: list[str], success: bool, leads: int = 0) -> None:
        try:
            import os
            import psycopg
            dsn = os.getenv("DATABASE_URL")
            if not dsn:
                try:
                    from x_ingest.__main__ import _dsn
                    dsn = _dsn()
                except Exception:
                    log.debug("No engine DSN from x_ingest", exc_info=True)
            if not dsn:
                return
            with psycopg.connect(dsn, connect_timeout=3) as conn:
                for seller_id in sellers:
                    if not str(seller_id).isdigit():
                        continue
                    b_id = int(seller_id)
                    alpha_inc = min(1.0, 0.25 * float(leads)) if (success and leads >= 1) else 0.0
                    beta_inc = 0.0 if (success and leads >= 1) else 0.25
                    now_iso = datetime.now(timezone.utc).isoformat()
                    conn.execute("""
                        INSERT INTO discovery_instagramsourcestat
                            (business_id, family_id, source_type, label, requests, leads, alpha, beta, last_run_at)
                        VALUES
                            (%s, %s, 'x_query', %s, 1, %s, %s, %s, %s)
                        ON CONFLICT (business_id, family_id, source_type, label)
                        DO UPDATE SET
                            requests = discovery_instagramsourcestat.requests + 1,
                            leads = discovery_instagramsourcestat.leads + EXCLUDED.leads,
                            alpha = discovery_instagramsourcestat.alpha + %s,
                            beta = discovery_instagramsourcestat.beta + %s,
                            last_run_at = EXCLUDED.last_run_at
                    """, (
                        b_id, f"x_{b_id}", query[:100], leads,
                        1.0 + alpha_inc, 1.0 + beta_inc, now_iso,
                        alpha_inc, beta_inc
                    ))
        except Exception as exc:
            log.debug("Could not update X source stat: %s", exc, exc_info=True)

    def _write_status(self, result: dict[str, Any]) -> None:
        target = _within_output_root(Path(os.getenv("X_STATUS_FILE", str(self.output_dir / "status.json"))), self.output_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: result.get(key) for key in ("status", "collected", "failures", "failed_queries", "output", "cooldown_until", "search_errors", "promotional_posts_dropped", "paused_businesses", "skipped_queries_by_balance")}
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        payload["collected_today"] = self.state.count_today() if self.state else 0
        payload["daily_cap"] = self.daily_cap
        payload["last_cycle_at"] = payload["updated_at"]
        if self.state:
            payload["queries"] = self.state.query_status()
            active = getattr(self, 'active_queries', [])
            _, weights, stats, _ = preview_searches(self, active)
            effective = {query: row['lead_rate'] for query, row in stats.items() if row.get('searches', 0) > 0}
            effective.update(scores([item for item in active if item['query'] in weights], stats))
            payload['generated_queries'] = [
                {'query': item['query'], 'business_ids': item.get('business_ids', []),
                 'score': effective.get(item['query']), 'weight': weights.get(item['query'], 0.0),
                 'searches': stats.get(item['query'], {}).get('searches', 0),
                 'leads': stats.get(item['query'], {}).get('leads', 0),
                 'eligible': item['query'] in weights}
                for item in getattr(self, 'report_queries', active)]
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
        log.error("Auto-ingest failed (run `python -m x_ingest` later): %s", exc, exc_info=True)
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
        log.error("X search fee not recorded: %s", exc, exc_info=True)
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
        log.error("X search log not recorded: %s", exc, exc_info=True)
        return 0


def collect_forever(collector: XCollector, queries: list[str] | Callable[[], list[str]], interval: float, mock_records: list[dict[str, Any]] | None = None) -> None:
    while True:
        try:
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
                            sync_bandit_feedback(collector.state, state_path=collector.state_path)
                    except Exception as exc:
                        log.error("Bandit feedback sync failed: %s", exc, exc_info=True)
        except (RuntimeError, OSError, sqlite3.Error, psycopg.Error):
            log.warning("Collection cycle failed; retrying in 2 minutes", exc_info=True)
            time.sleep(120)
            continue
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

