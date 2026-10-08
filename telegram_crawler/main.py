"""CLI entry point of the Telegram crawler (archives raw group messages for need_engine).

    python -m telegram_crawler.main --link https://t.me/group_name [--link ...] [--links-file groups.txt]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from telegram_crawler.config import settings
from telegram_crawler.monitor import CrawlerMonitor
from telegram_crawler.telegram_client import create_telegram_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("telegram_crawler.main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive messages of Telegram groups for need_engine.")
    parser.add_argument("--link", action="append", default=[],
                        help="Telegram group link (t.me/+hash, t.me/name or @name). Repeat for several groups.")
    parser.add_argument("--links-file", default=None, help="Text file with one group link per line (# comments allowed).")
    parser.add_argument("--no-resume", action="store_true", help="Ignore the last scanned message id and rescan the backfill window.")
    parser.add_argument("--hours", type=float, default=settings.backfill_hours,
                        help=f"Hours of history to archive on start (default: {settings.backfill_hours}).")
    parser.add_argument("--limit", type=int, default=settings.backfill_limit,
                        help=f"Max historical messages per group (default: {settings.backfill_limit}).")
    parser.add_argument("--db-path", default=settings.db_path, help=f"SQLite archive path (default: {settings.db_path}).")
    parser.add_argument("--no-live", action="store_true", help="Only archive history and exit.")
    return parser.parse_args()


async def async_main() -> None:
    args = parse_args()
    group_links = list(args.link)
    if args.links_file:
        with open(args.links_file, encoding="utf-8") as fh:
            group_links += [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]
    if not group_links:
        entered = input("Enter Telegram Group link or @username: ").strip()
        if entered:
            group_links.append(entered)
    if not group_links:
        print("Error: at least one group link is required.", file=sys.stderr)
        sys.exit(1)

    try:
        settings.require("api_id", "api_hash")
    except ValueError as exc:
        print(f"\nConfiguration Error: {exc}\nSet TG_API_ID and TG_API_HASH in .env.\n", file=sys.stderr)
        sys.exit(1)

    client = create_telegram_client()
    await client.start()
    me = await client.get_me()
    log.info("Authenticated as: %s (id=%s)", me.first_name, me.id)

    monitor = CrawlerMonitor(client=client, group_links=group_links, backfill_hours=args.hours,
                             backfill_limit=args.limit, db_path=args.db_path, resume=not args.no_resume)
    try:
        await monitor.run(live=not args.no_live)
    except KeyboardInterrupt:
        log.info("Monitoring stopped by user.")
    finally:
        log.info("Crawler stats: %s", monitor.stats)
        await client.disconnect()


def main() -> None:
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
