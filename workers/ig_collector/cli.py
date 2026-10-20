from __future__ import annotations

import argparse
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
    parser.add_argument("--backend", choices=("instaloader", "hikerapi", "mock"), default=os.getenv("IG_BACKEND", "instaloader"))
    parser.add_argument("--seeds", default=os.getenv("IG_SEEDS_FILE", "data/ig_seeds.json"))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=float(os.getenv("IG_COLLECT_INTERVAL_SECONDS", "900")))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock", action="store_true", help="force fixture backend with isolated output and state")
    parser.add_argument("--keep-rejected", action="store_true", help="save rejection reasons without post text or identities")
    parser.add_argument("--output-dir", default=os.getenv("IG_COLLECT_OUTPUT_DIR", "data/ig_collected"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if args.once and args.loop:
        parser.error("Choose either --once or --loop.")

    is_mock = args.mock or args.backend == "mock"
    if not (args.once or args.loop or args.dry_run or args.mock):
        parser.error("Choose --once, --loop, --dry-run, or --mock.")
    try:
        if is_mock:
            backend = MockInstagramBackend()
            # Mock mode is reproducible and cannot read private seeds or touch a
            # live backend. Its own deterministic seed always selects the fixture.
            seeds = [{"kind": "account", "value": "demo_demand", "limit": 12}]
            comments = {"mode": "signals", "max_posts_per_run": 5, "max_per_post": 50}
            output_dir = project_root / "data" / "ig_collected_mock"
            state_path = project_root / "output" / "ig_collector_mock_state.sqlite3"
        else:
            backend = _backend(args.backend)
            seed_path = Path(args.seeds)
            if not seed_path.is_absolute():
                seed_path = project_root / seed_path
            seeds, comments = InstagramCollector.load_seeds(seed_path)
            output_dir = Path(args.output_dir)
            if not output_dir.is_absolute():
                output_dir = project_root / output_dir
            state_path = None
        collector = InstagramCollector(backend, output_dir=output_dir, state_path=state_path)
    except (ValueError, Fatal) as exc:
        parser.error(str(exc))

    def cycle() -> dict[str, Any]:
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
    return 0 if status in {"DRY_RUN", "COMPLETED", "DAILY_CAP_REACHED"} else 1
