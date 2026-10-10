from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from .client import XCliError
from .worker import XCollector, auto_ingest_enabled, charge_searches, collect_forever, record_searches, ingest_new, latest_offset


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Read-only X tweet collector (JSONL worker)")
    parser.add_argument("--queries", default=os.getenv("X_COLLECT_QUERIES_FILE"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--print-queries", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=float, default=float(os.getenv("X_COLLECT_INTERVAL_SECONDS", "300")))
    parser.add_argument("--no-ingest", action="store_true", help="do not load new posts into the database")
    parser.add_argument("--output-dir", default=os.getenv("X_COLLECT_OUTPUT_DIR", "data/x_collected"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if args.mock:
        queries = XCollector.load_queries(args.queries) or ["mock"]
        query_provider = queries
    else:
        try:
            from .query_source import collector_queries

            queries = collector_queries(args.queries)
            query_provider = lambda: collector_queries(args.queries)
        except RuntimeError as exc:
            parser.error(str(exc))
    if not queries and args.loop and not args.mock:
        # per-product mode with no product switched on yet: keep running, products are re-read every cycle
        logging.warning("No X search queries yet (no product has «جستجوی مشتری در X» on); waiting for products")
    elif not queries:
        parser.error(f"No queries found. Turn X search on for a product in the seller panel, or add UTF-8 queries "
                     f"(one per line) to {args.queries}.")
    if args.print_queries:
        grouped = {}
        for item in queries:
            query = item["query"] if isinstance(item, dict) else item
            kind = item.get("kind", "product") if isinstance(item, dict) else "product"
            if isinstance(item, dict) and item.get("product_ids"):
                query = f"[product {','.join(item['product_ids'])}] {query}"
            grouped.setdefault(kind, []).append(query)
        print(json.dumps({kind: {"count": len(items), "queries": items} for kind, items in grouped.items()}, ensure_ascii=False, indent=2))
        return 0
    queries = [item for item in queries if not (item["query"] if isinstance(item, dict) else item).lstrip().startswith("-")]
    if not queries and not args.loop:
        parser.error("All X search queries were skipped because they start with '-'.")
    output_dir = "data/x_collected_mock" if args.mock else args.output_dir
    state_path = "output/x_collector_mock_state.sqlite3" if args.mock else None
    collector = XCollector(output_dir=output_dir, state_path=state_path)
    mock_records = None
    if args.mock:
        fixture = Path(__file__).resolve().parent / "tests" / "fixtures" / "x_tweets.json"
        if not fixture.is_file():
            parser.error(f"Mock fixture {fixture} not found.")
        mock_records = json.loads(fixture.read_text(encoding="utf-8"))
    if args.loop:
        if args.dry_run:
            _print_commands(queries)
            print(json.dumps(collector.run(queries, dry_run=True), ensure_ascii=False, indent=2))
            return 0
        try:
            if args.no_ingest:
                os.environ["X_AUTO_INGEST"] = "false"
            collect_forever(collector, query_provider, max(1.0, args.interval), mock_records)
        except XCliError as exc:
            logging.error("Collector loop stopped: %s", exc)
            return 1
        except KeyboardInterrupt:
            logging.info("Collector loop stopped by user")
        return 0
    if not (args.once or args.dry_run or args.mock):
        parser.error("Choose --once, --loop, --dry-run, or --mock.")
    try:
        if args.dry_run:
            _print_commands(queries)
        offset = latest_offset(collector)
        result = collector.run(queries, dry_run=args.dry_run, mock_records=mock_records)
        if not (args.dry_run or args.mock or args.no_ingest) and auto_ingest_enabled():
            result["ingested"] = ingest_new(collector, offset)
        if not (args.dry_run or args.mock):
            charge_searches(getattr(collector, "searches", []))
            record_searches(getattr(collector, "searches", []))
    except XCliError as exc:
        logging.error("%s", exc)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"DRY_RUN", "COMPLETED", "DAILY_CAP_REACHED"} else 1


def _print_commands(queries: list[str]) -> None:
    from . import cli_mapping

    for query in queries:
        value = query["query"] if isinstance(query, dict) else query
        if value.lstrip().startswith("-"):
            logging.warning("Skipping query starting with '-' in dry-run: %s", query)
            continue
        command = [cli_mapping.CLI_COMMAND, cli_mapping.CLI_SEARCH_SUBCOMMAND, value, "-t", cli_mapping.CLI_TIME_FILTER,
                   cli_mapping.CLI_EXCLUDE_RETWEETS_FLAG, cli_mapping.CLI_EXCLUDE_RETWEETS_VALUE,
                   cli_mapping.CLI_LIMIT_FLAG, str(int(os.getenv("X_COLLECT_MAX_PER_QUERY", "50"))), cli_mapping.CLI_OUTPUT_FLAG]
        print("CLI dry-run command: " + subprocess.list2cmdline(command), file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
