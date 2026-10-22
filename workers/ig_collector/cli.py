from __future__ import annotations

import argparse
import importlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from .backends.base import Fatal
from .backends.mock import MockInstagramBackend
from .worker import InstagramCollector
from shared.instagram_manual_post import ManualPostRequest


def _backend(name: str):
    if name == "mock":
        return MockInstagramBackend()
    if name == "instaloader":
        from .backends.instaloader_backend import InstaloaderBackend
        return InstaloaderBackend()
    if name == "hikerapi":
        from .backends.hikerapi_backend import HikerAPIBackend
        return HikerAPIBackend()
    raise ValueError("unsupported backend")


def main(argv: list[str] | None = None) -> int:
    return _main(argv, project_root=Path(__file__).resolve().parents[2])


def _main(argv: list[str] | None = None, *, project_root: Path) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Read-only Instagram collector (Persian intent prefilter + JSONL)")
    parser.add_argument("--backend", choices=("instaloader", "hikerapi", "mock"), default=os.getenv("IG_BACKEND") or None)
    parser.add_argument("--seeds", default=os.getenv("IG_SEEDS_FILE", "data/ig_seeds.json"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=float(os.getenv("IG_COLLECT_INTERVAL_SECONDS", "900")))
    parser.add_argument("--loop-interval", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock", action="store_true", help="force fixture backend with isolated output and state")
    parser.add_argument("--keep-rejected", action="store_true", help="save rejection reasons without post text or identities")
    parser.add_argument("--output-dir", default=os.getenv("IG_COLLECT_OUTPUT_DIR", "data/ig_collected"))
    parser.add_argument("--post", help="operator-supplied Instagram post URL")
    parser.add_argument("--business-id", type=int)
    parser.add_argument("--category-id", type=int)
    parser.add_argument("--product-id", type=int)
    parser.add_argument("--max-comments", type=int, default=50)
    parser.add_argument("--daily-comment-cap", type=int, default=100)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--refresh", action="store_true", help="refetch comments while deduplicating comment IDs")
    parser.add_argument("--comment-pipeline-factory", default=os.getenv("IG_COMMENT_PIPELINE_FACTORY"),
                        help="module:function returning (read_only_knowledge_reader, jev_evaluator)")
    args = parser.parse_args(argv)
    if args.post and not (args.once or args.dry_run or args.mock):
        parser.error("Use --once, --dry-run, or --mock with --post.")
    if args.post and (args.business_id is None or (args.category_id is None and args.product_id is None)):
        parser.error("--post requires --business-id and at least one of --category-id or --product-id.")
    if args.post and (not 1 <= args.max_comments <= 500 or not 1 <= args.daily_comment_cap <= 5000
                      or not 1 <= args.window_days <= 90):
        parser.error("--max-comments must be 1..500, --daily-comment-cap 1..5000, and --window-days 1..90.")
    if args.loop_interval is not None:
        args.interval = args.loop_interval
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if args.once and args.loop:
        parser.error("Choose either --once or --loop.")
    if not args.backend and not args.mock:
        parser.error("Set IG_BACKEND to hikerapi or instaloader before starting live collection.")

    isolated_mock = args.mock
    if not (args.once or args.loop or args.dry_run or args.mock):
        parser.error("Choose --once, --loop, --dry-run, or --mock.")
    try:
        if isolated_mock:
            backend = MockInstagramBackend()
            # Mock mode is reproducible and cannot read private seeds or touch a
            # live backend. Its own deterministic seed always selects the fixture.
            seeds = ([{"kind": "post", "value": args.post, "business_id": args.business_id,
                       "category_id": args.category_id, "product_id": args.product_id,
                       "max_comments": args.max_comments, "daily_comment_cap": args.daily_comment_cap,
                       "window_days": args.window_days,
                       "refresh": args.refresh}]
                     if args.post else [{"kind": "account", "value": "demo_demand", "limit": 12}])
            comments = {"mode": "signals", "max_posts_per_run": 5, "max_per_post": 50}
            output_dir = project_root / "data" / "ig_collected_mock"
            state_path = project_root / "output" / "ig_collector_mock_state.sqlite3"
        else:
            backend = _backend(args.backend)
            if args.post:
                seeds = [{"kind": "post", "value": args.post, "business_id": args.business_id,
                          "category_id": args.category_id, "product_id": args.product_id,
                          "max_comments": args.max_comments, "daily_comment_cap": args.daily_comment_cap,
                          "window_days": args.window_days,
                          "refresh": args.refresh}]
                comments = {"mode": "all", "policy": "seller_post", "max_posts_per_run": 1,
                            "max_per_post": args.max_comments, "window_days": args.window_days}
            else:
                seed_path = Path(args.seeds)
                if not seed_path.is_absolute():
                    seed_path = project_root / seed_path
                seeds, comments = InstagramCollector.load_seeds(seed_path)
            output_dir = Path(args.output_dir)
            if not output_dir.is_absolute():
                output_dir = project_root / output_dir
            state_path = None
        knowledge_reader = jev = None
        if args.comment_pipeline_factory:
            module_name, separator, factory_name = args.comment_pipeline_factory.partition(":")
            if not separator or not module_name or not factory_name:
                parser.error("--comment-pipeline-factory must be written as module:function")
            factory = getattr(importlib.import_module(module_name), factory_name)
            knowledge_reader, jev = factory()
        collector = InstagramCollector(backend, output_dir=output_dir, state_path=state_path,
                                      knowledge_reader=knowledge_reader, jev=jev)
    except (ValueError, Fatal) as exc:
        parser.error(str(exc))

    def cycle() -> dict[str, Any]:
        if args.post:
            if args.dry_run:
                return {"status": "DRY_RUN", "comments_mode": "manual_post",
                        "max_comments": args.max_comments, "window_days": args.window_days}
            manual = collector.run_manual_post(ManualPostRequest(
                post_url=args.post, business_id=args.business_id,
                category_id=args.category_id, product_id=args.product_id,
                max_comments=args.max_comments, daily_comment_cap=args.daily_comment_cap,
                window_days=args.window_days,
                refresh=args.refresh,
            ))
            return {"status": manual.status, "comments_fetched": manual.comments_fetched,
                    "comments_evaluated": manual.comments_evaluated,
                    "human_review_comments": manual.human_review_comments,
                    **({"reason": manual.reason} if manual.reason else {})}
        return collector.run(seeds, comments, dry_run=args.dry_run, keep_rejected=args.keep_rejected)

    if args.loop and not args.dry_run:
        interval = max(1.0, args.interval)
        try:
            while True:
                result = cycle()
                print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                if result["status"] in {"CIRCUIT_OPEN", "RATE_LIMITED", "AUTH_FAILED", "FAILED"}:
                    return _exit_code(result["status"])
                time.sleep(interval)
        except KeyboardInterrupt:
            logging.info("Instagram collection loop stopped by user")
            return 0
    result = cycle()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return _exit_code(result["status"])


def _exit_code(status: str) -> int:
    return 0 if status in {
        "DRY_RUN", "COMPLETED", "DAILY_CAP_REACHED", "completed",
        "completed_with_review", "already_fetched", "outside_time_window",
    } else 1
