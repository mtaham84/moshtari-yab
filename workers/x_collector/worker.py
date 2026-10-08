from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import cli_mapping as mapping
from .client import XCliClient, XCliError

log = logging.getLogger("x_collector")
RATE_LIMIT_COOLDOWN_SECONDS = 15 * 60


def _first(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_tweet(raw: dict[str, Any]) -> dict[str, Any] | None:
    tweet_id = _first(raw, mapping.OUTPUT_ID_KEYS)
    text = _first(raw, mapping.OUTPUT_TEXT_KEYS)
    handle = _first(raw, mapping.OUTPUT_HANDLE_KEYS)
    if not tweet_id or not handle or not isinstance(text, str) or not text.strip():
        missing = []
        if not tweet_id:
            missing.append("id")
        if not handle:
            missing.append("handle")
        if not isinstance(text, str) or not text.strip():
            missing.append("text")
        log.warning("Skipping tweet with missing required fields: %s", ", ".join(missing))
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
        "author_name": _first(raw, mapping.OUTPUT_NAME_KEYS),
        "created_at": str(created_at) if created_at else None,
        "url": tweet_url,
        "metadata": {"collector": "agent-reach-cli", "raw_cli": raw},
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
            connection.commit()

    @staticmethod
    def _key(record: dict[str, Any]) -> str:
        if record.get("id"):
            value = "id:" + str(record["id"])
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

    @staticmethod
    def load_queries(query_file: str | Path | None = None) -> list[str]:
        path = Path(query_file or os.getenv("X_COLLECT_QUERIES_FILE", "data/x_queries.txt"))
        if not path.is_file():
            return []
        lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
        return list(dict.fromkeys(line for line in lines if line and not line.startswith("#")))

    def run(self, queries: list[str], dry_run: bool = False, mock_records: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
            return {"status": "DRY_RUN", "queries": queries, "limit_per_query": self.max_per_query, "daily_remaining": max(0, self.daily_cap - collected_today), "output": str(output_path), "cli_called": False}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state = SeenStore(self.state_path)
        collected, failures, failed_queries, consecutive_failures = [], 0, 0, 0
        stop_reason = None
        daily_remaining = max(0, self.daily_cap - self.state.count_today())
        cooldown = self.state.get_setting("rate_limit_until")
        if cooldown and datetime.now(timezone.utc) < datetime.fromisoformat(cooldown):
            return {"status": "RATE_LIMITED", "collected": 0, "failures": 0, "output": str(output_path), "cooldown_until": cooldown}
        if daily_remaining == 0:
            return {"status": "DAILY_CAP_REACHED", "collected": 0, "output": str(output_path)}
        latest_path = self.output_dir / "latest.jsonl"
        with output_path.open("a+", encoding="utf-8") as stream, latest_path.open("a", encoding="utf-8") as latest:
            stream.seek(0)
            already_written = set()
            for line in stream:
                try:
                    existing = json.loads(line)
                    if existing.get("id"):
                        already_written.add(str(existing["id"]))
                except json.JSONDecodeError:
                    continue
            stream.seek(0, os.SEEK_END)
            for query_index, query in enumerate(queries):
                if len(collected) >= daily_remaining:
                    break
                retry = 0
                while retry <= self.max_retries:
                    try:
                        if mock_records is not None:
                            raw_items = mock_records
                        else:
                            raw_items = self.client.search(query, min(self.max_per_query, daily_remaining - len(collected)))
                        consecutive_failures = 0
                        for raw in raw_items[: self.max_per_query]:
                            normalized = normalize_tweet(raw)
                            if normalized is None or normalized["id"] in already_written or self.state.has_seen(normalized):
                                continue
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
                            already_written.add(normalized["id"])
                            collected.append(normalized)
                        break
                    except XCliError as exc:
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
        return {"status": status, "collected": len(collected), "failures": failures, "failed_queries": failed_queries, "output": str(output_path)}


def collect_forever(collector: XCollector, queries: list[str], interval: float, mock_records: list[dict[str, Any]] | None = None) -> None:
    while True:
        result = collector.run(queries, mock_records=mock_records)
        log.info("Collection cycle finished: status=%s collected=%s", result["status"], result.get("collected", 0))
        if result["status"] in {"CIRCUIT_OPEN", "RATE_LIMITED", "AUTH_FAILED", "FAILED"}:
            raise XCliError(f"Collector stopped with status {result['status']}")
        time.sleep(interval)
