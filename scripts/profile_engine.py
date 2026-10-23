"""Profile the engine pipeline with generated X messages and PostgreSQL-backed state.

The script uses deterministic mock LLM/embeddings and removes its generated JSONL files after a run.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import logging
import os
import sys
import time
import tracemalloc
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

import psycopg

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("PGCONNECT_TIMEOUT", "3")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from need_engine.config import EngineConfig
from need_engine.engine import NeedEngine
from need_engine.mock import mock_llm


STAGES = ("ingest", "prefilter", "extract", "embed", "match", "emit")


class QueryCounter:
    def __init__(self) -> None:
        self.counts: dict[str, int] = defaultdict(int)
        self.unassigned = 0

    def record(self, _sql: str) -> None:
        stage = _active_stage
        if stage:
            self.counts[stage] += 1
        else:
            self.unassigned += 1


_active_stage: str | None = None


class StageTimer:
    def __init__(self, timings: dict[str, float], queries: QueryCounter, name: str) -> None:
        self.timings, self.queries, self.name = timings, queries, name

    def __enter__(self):
        global _active_stage
        self.previous_stage = _active_stage
        self.started = time.perf_counter()
        _active_stage = self.name

    def __exit__(self, exc_type, exc, tb):
        global _active_stage
        self.timings[self.name] += time.perf_counter() - self.started
        _active_stage = self.previous_stage


def generated_messages(count: int) -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        {
            "chat_id": "x:public",
            "message_id": index + 1,
            "author_id": f"author-{index + 1}",
            "author_name": f"کاربر {index + 1}",
            "author_username": f"user{index + 1}",
            "text": f"دنبال خرید لپ تاپ سبک برای کار روزانه با بودجه {30 + index % 40} میلیون هستم {index}",
            "date": (now - timedelta(minutes=count - index)).isoformat(),
            "platform": "x",
            "kind": "post",
            "lang": "fa",
        }
        for index in range(count)
    ]


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


@contextmanager
def _profile_paths() -> Iterator[tuple[Path, Path, Path]]:
    stem = ROOT / f".need-engine-profile-{os.getpid()}-{time.time_ns()}"
    paths = (stem.with_suffix(".messages.jsonl"), stem.with_suffix(".products.jsonl"),
             stem.with_suffix(".opportunities.jsonl"))
    try:
        yield paths
    finally:
        for path in paths:
            path.unlink(missing_ok=True)


def install_query_counter(counter: QueryCounter) -> Callable[[], None]:
    original_cursor_execute = psycopg.Cursor.execute
    original_cursor_executemany = psycopg.Cursor.executemany

    def cursor_execute(cursor, query, *args, **kwargs):
        counter.record(str(query))
        return original_cursor_execute(cursor, query, *args, **kwargs)

    def cursor_executemany(cursor, query, *args, **kwargs):
        counter.record(str(query))
        return original_cursor_executemany(cursor, query, *args, **kwargs)

    psycopg.Cursor.execute = cursor_execute
    psycopg.Cursor.executemany = cursor_executemany

    def restore() -> None:
        psycopg.Cursor.execute = original_cursor_execute
        psycopg.Cursor.executemany = original_cursor_executemany

    return restore


def _time_method(obj, method_name: str, stage: str, timings: dict[str, float], queries: QueryCounter) -> None:
    original = getattr(obj, method_name)

    def timed(*args, **kwargs):
        with StageTimer(timings, queries, stage):
            return original(*args, **kwargs)

    setattr(obj, method_name, timed)


def profile(count: int) -> dict:
    if count < 1:
        raise ValueError("message count must be positive")

    with _profile_paths() as (messages_path, products_path, output_path):
        _write_jsonl(messages_path, generated_messages(count))
        _write_jsonl(products_path, [{
            "product_id": "laptop-1",
            "title": "لپ تاپ سبک",
            "description": "لپ تاپ کم وزن برای کار روزانه و برنامه نویسی",
            "product_type": "کالای دیجیتال",
            "price_toman": 45000000,
            "card_override": {"what_it_is": "لپ تاپ", "aliases": ["نوت بوک"], "problems_solved": ["کار روزانه"]},
        }])

        schema = f"need_engine_profile_{os.getpid()}_{int(time.time())}"
        cfg = EngineConfig()
        cfg.messages_source = f"jsonl:{messages_path}"
        cfg.products_source = f"jsonl:{products_path}"
        cfg.embed_backend = "hash"
        cfg.source_access = "open"
        cfg.state_schema = schema
        cfg.output_jsonl = str(output_path)
        cfg.x_min_chars = 1
        cfg.x_max_per_run = count
        cfg.x_max_posts_per_author_per_run = count
        cfg.x_enabled = False
        cfg.write_replies = False
        cfg.model_registry = False
        cfg.trigger_count = 1
        cfg.window_size = count
        cfg.max_workers = 1

        def profile_mock(stage: str, system: str, user: str) -> dict:
            if stage == "need_extraction_x_batch":
                posts = json.loads(user)
                return {"needs": [
                    {"post_id": post["post_id"], "author_id": post["author_id"],
                     "evidence_message_ids": [post["post_id"]], "label": "explicit_need",
                     "is_opportunity": True, "buyer_intent_confirmed": True,
                     "promotional_content": False, "author_type": "individual",
                     "situation": post["text"][:80], "need": post["text"][:60],
                     "solution_queries": ["لپ تاپ سبک"], "problem_queries": ["کار روزانه"],
                     "requirements": [{"text": "سبک", "must": True}], "strength": "medium"}
                    for post in posts
                ]}
            if stage == "need_extraction":
                data = mock_llm(stage, system, user)
                for need in data.get("needs", []):
                    need.update(buyer_intent_confirmed=True, promotional_content=False, author_type="individual")
                return data
            return mock_llm(stage, system, user)

        timings: dict[str, float] = defaultdict(float)
        queries = QueryCounter()
        engine = None
        restore_query_counter = install_query_counter(queries)
        tracemalloc.start()
        try:
            engine = NeedEngine(cfg, mock_llm=profile_mock)
            engine.llm.registry.enabled = False
            for message in engine.messages.msgs:
                message.platform = "x"

            _time_method(engine, "_select_x_pending", "prefilter", timings, queries)
            _time_method(engine.emb, "encode", "embed", timings, queries)
            _time_method(engine, "_match", "match", timings, queries)
            _time_method(engine, "_emit", "emit", timings, queries)
            _time_method(engine, "ingest", "ingest", timings, queries)
            import need_engine.engine as engine_module
            _time_method(engine_module, "extract_x_batch", "extract", timings, queries)
            _time_method(engine_module, "extract_x_product_batch", "extract", timings, queries)

            engine.access.refresh = lambda: None
            engine.styles = {}
            engine.refresh_x_products = lambda: None
            engine.release = lambda report: None
            engine.expire = lambda report, now=None: None

            report = engine.run_once(flush=True)

            _, peak_memory = tracemalloc.get_traced_memory()
            return {
                "messages": count,
                "x_max_per_run_override": cfg.x_max_per_run,
                "x_max_posts_per_author_per_run_override": cfg.x_max_posts_per_author_per_run,
                "window_size_override": cfg.window_size,
                "processed": report.analysed_messages,
                "opportunities": len(report.opportunities),
                "timings_seconds": {stage: round(timings.get(stage, 0.0), 6) for stage in STAGES},
                "queries_per_stage": {stage: queries.counts.get(stage, 0) for stage in STAGES},
                "unassigned_queries": queries.unassigned,
                "peak_memory_mib": round(peak_memory / (1024 * 1024), 2),
                "errors": len(report.errors),
            }
        finally:
            tracemalloc.stop()
            if engine is not None:
                try:
                    engine.store.conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
                finally:
                    engine.store.close()
            restore_query_counter()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--messages", type=int, default=5000)
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    try:
        result = profile(args.messages)
    except psycopg.OperationalError as error:
        print(f"Cannot profile need_engine: PostgreSQL is unavailable ({type(error).__name__}).")
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Messages generated: {result['messages']} · processed: {result['processed']} · opportunities: {result['opportunities']}")
        print(f"Peak traced memory: {result['peak_memory_mib']} MiB · errors: {result['errors']}")
        print("Stage             Seconds       DB queries")
        for stage in STAGES:
            print(f"{stage:<17} {result['timings_seconds'][stage]:>9.6f} {result['queries_per_stage'][stage]:>15}")
        print(f"Unassigned DB queries: {result['unassigned_queries']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
