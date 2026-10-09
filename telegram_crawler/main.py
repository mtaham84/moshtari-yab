"""CLI entry point of the Telegram crawler (archives raw group messages for need_engine).

    python -m telegram_crawler.main                 # groups come from the panel («جوامع آنلاین»), no restart needed
    python -m telegram_crawler.main --login         # interactive Telegram login once (stores the session)
    python -m telegram_crawler.main --link https://t.me/group_name [--link ...] [--links-file groups.txt] [--no-panel]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from telegram_crawler.config import settings
from telegram_crawler.monitor import CrawlerMonitor
from telegram_crawler.panel import PanelCommunities
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
    parser.add_argument("--no-profiles", action="store_true", help="Do not fetch user bios (users.GetFullUser).")
    parser.add_argument("--no-live", action="store_true", help="Only archive history and exit.")
    parser.add_argument("--no-panel", action="store_true",
                        help="Do not read groups from the panel communities table (TG_PANEL_COMMUNITIES=false).")
    parser.add_argument("--login", action="store_true", help="Log in to Telegram interactively, save the session and exit.")
    return parser.parse_args()


async def _wait_and_exit(message: str) -> None:
    """Missing setup in a non-interactive container: explain, wait (avoid a restart storm), exit."""
    log.error(message)
    await asyncio.sleep(float(os.getenv("TG_SETUP_RETRY_SECONDS", "120")))
    sys.exit(1)


async def async_main() -> None:
    args = parse_args()
    use_panel = settings.panel_enabled and not args.no_panel and not args.login
    group_links = list(args.link)
    if args.links_file and os.path.isfile(args.links_file):
        with open(args.links_file, encoding="utf-8") as fh:
            group_links += [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]
    if not group_links and not use_panel and not args.login:
        print("Error: at least one group link is required (or enable the panel communities).", file=sys.stderr)
        sys.exit(1)

    try:
        settings.require("api_id", "api_hash")
    except ValueError as exc:
        await _wait_and_exit(f"Configuration error: {exc}. Set TG_API_ID and TG_API_HASH in .env.")

    client = create_telegram_client()
    if args.login or sys.stdin.isatty():
        await client.start()
    else:
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            await _wait_and_exit("Telegram session is not logged in. Run once: docker compose run --rm crawler login")
    me = await client.get_me()
    log.info("Authenticated as: %s (id=%s)", me.first_name, me.id)
    if args.login:
        await client.disconnect()
        log.info("Session saved. Start the crawler with: docker compose up -d crawler")
        return

    monitor = CrawlerMonitor(client=client, group_links=group_links, backfill_hours=args.hours,
                             backfill_limit=args.limit, resume=not args.no_resume,
                             fetch_profiles=False if args.no_profiles else None,
                             panel=PanelCommunities.connect() if use_panel else None)
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
