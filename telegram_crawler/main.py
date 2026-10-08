"""CLI entry point for running the Telegram Lead Crawler."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from telegram_crawler.config import settings
from telegram_crawler.detector import KeywordTargetDetector
from telegram_crawler.models import LeadContext
from telegram_crawler.monitor import CrawlerMonitor
from telegram_crawler.telegram_client import create_telegram_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("telegram_crawler.main")


async def example_lead_callback(lead: LeadContext) -> None:
    """
    Sample structured handler that receives the detected lead.
    In the future, you can send this data to your CRM, webhook, or seller notification bot.
    """
    print("\n" + "=" * 65)
    print("📥 CANDIDATE QUEUED FOR ANALYSIS")
    print(f"Group:        {lead.group.title} (ID: {lead.group.group_id})")
    print(f"User:         {lead.user.display_name} (ID: {lead.user.user_id}, Username: @{lead.user.username or 'N/A'})")
    print(f"Target Msg:   \"{lead.target_message.text}\" (ID: {lead.target_message.message_id})")
    print(f"Date:         {lead.target_message.date}")

    if lead.previous_messages:
        print("\n--- Previous Chat Context (Last messages before target) ---")
        for i, prev in enumerate(lead.previous_messages, 1):
            print(f"  {i}. [{prev.sender_name or 'User'}] (id={prev.message_id}): {prev.text[:80]}")

    if lead.reply_thread.parent_messages:
        print("\n--- Thread Reply Ancestors (Chain leading to target) ---")
        for i, parent in enumerate(lead.reply_thread.parent_messages, 1):
            print(f"  ↑ Parent {i}: [{parent.sender_name or 'User'}]: {parent.text[:80]}")

    if lead.reply_thread.child_replies:
        print("\n--- Thread Child Replies (Responses to target) ---")
        for i, child in enumerate(lead.reply_thread.child_replies, 1):
            print(f"  ↓ Reply {i}: [{child.sender_name or 'User'}]: {child.text[:80]}")

    print("=" * 65 + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Telegram Lead Crawler: Monitor Telegram groups for potential customer leads."
    )
    parser.add_argument(
        "--link",
        action="append",
        default=[],
        help="Telegram group link (t.me/+hash, t.me/name or @name). Repeat for several groups.",
    )
    parser.add_argument(
        "--links-file",
        type=str,
        default=None,
        help="Text file with one group link per line (# comments allowed).",
    )
    parser.add_argument(
        "--no-analysis",
        action="store_true",
        help="Archive and extract only; do not enqueue candidates in the analysis inbox.",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore the last scanned message id and rescan the backfill window.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print every queued candidate.",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=settings.backfill_hours,
        help=f"Hours of past message history to scan (default: {settings.backfill_hours}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=settings.backfill_limit,
        help=f"Max count of historical messages to scan (default: {settings.backfill_limit}).",
    )
    parser.add_argument(
        "--context-count",
        type=int,
        default=settings.context_msg_count,
        help=f"Number of preceding context messages to fetch (default: {settings.context_msg_count}).",
    )
    parser.add_argument(
        "--keywords",
        type=str,
        default=None,
        help="Comma-separated list of target keywords to override defaults.",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=settings.db_path,
        help=f"SQLite database file path (default: {settings.db_path}).",
    )
    parser.add_argument(
        "--no-live",
        action="store_true",
        help="Only run historical backfill scan and exit without starting live monitoring.",
    )
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

    # Check Telegram credentials
    try:
        settings.require("api_id", "api_hash")
    except ValueError as exc:
        print(f"\nConfiguration Error: {exc}", file=sys.stderr)
        print("Please configure TG_API_ID and TG_API_HASH in your .env file.\n", file=sys.stderr)
        sys.exit(1)

    detector = None
    if args.keywords:
        custom_kw = tuple(k.strip() for k in args.keywords.split(",") if k.strip())
        detector = KeywordTargetDetector(custom_kw)
        log.info("Using custom keywords: %s", custom_kw)

    log.info("Starting Telegram Client...")
    client = create_telegram_client()
    await client.start()

    me = await client.get_me()
    log.info("Authenticated as: %s (id=%s)", me.first_name, me.id)

    monitor = CrawlerMonitor(
        client=client,
        group_links=group_links,
        detector=detector,
        backfill_hours=args.hours,
        backfill_limit=args.limit,
        context_msg_count=args.context_count,
        on_lead_detected=None if args.quiet else example_lead_callback,
        db_path=args.db_path,
        enqueue=not args.no_analysis,
        resume=not args.no_resume,
    )

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
