"""Command line entry point for the first Instagram discovery step."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .analyzer import analyze_posts, load_categories
from .collector import CollectorError, OpenCLICollector, Source
from .labeling import export_labels, print_report, report_labels


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _sources(config: dict, args) -> list[Source]:
    cli_usernames = args.user or []
    usernames = cli_usernames or config.get("usernames") or []
    if isinstance(usernames, str):
        usernames = [usernames]
    sources = [Source("user", str(username).strip().lstrip("@")) for username in usernames if str(username).strip()]
    # An explicit --user call is a focused run. Do not silently add the
    # default Explore feed configured for full scheduled/normal runs.
    include_explore = args.explore or (not cli_usernames and config.get("explore", not usernames))
    if include_explore:
        sources.append(Source("explore", "instagram"))
    if not sources:
        raise ValueError("No sources configured. Provide --user or enable explore in sources.json.")
    return sources


def _category_rows(payload: Any) -> list[dict]:
    if isinstance(payload, dict):
        payload = payload.get("categories", [])
    if not isinstance(payload, list):
        raise ValueError("categories file must contain a list or an object with a categories list.")
    return load_categories([row for row in payload if isinstance(row, dict)])


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m instagram_discovery",
        description="Read candidate Instagram posts through Agent Reach/OpenCLI and flag likely product advertisements.",
    )
    parser.add_argument("--sources", type=Path, default=Path("instagram_discovery/sources.json"),
                        help="JSON file with usernames, explore and limit_per_source.")
    parser.add_argument("--categories", type=Path, default=Path("instagram_discovery/categories.json"),
                        help="Optional JSON category/keyword list; falls back to categories.example.json.")
    parser.add_argument("--django-categories", action="store_true",
                        help="Read active categories from the Django database instead of the JSON categories file.")
    parser.add_argument("--user", action="append", help="Inspect this Instagram username; may be repeated.")
    parser.add_argument("--explore", action="store_true", help="Also read the current Instagram Explore candidates.")
    parser.add_argument("--limit", type=int, help="Maximum number of posts to request per source (1-100).")
    parser.add_argument("--output-dir", type=Path, default=Path("instagram_discovery/output"))
    parser.add_argument("--opencli-bin", help="OpenCLI executable path; defaults to OPENCLI_BIN or opencli.")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout for each OpenCLI call in seconds.")
    return parser


def _label_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m instagram_discovery")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("label-export", help="Export comment rows for manual flow and lead labels.")
    export.add_argument("--in", dest="input", required=True, type=Path)
    export.add_argument("--out", required=True, type=Path)
    export.add_argument("--sample", type=int, default=200)
    report = commands.add_parser("label-report", help="Summarize labelled comments and classifier precision.")
    report.add_argument("--in", dest="input", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"label-export", "label-report"}:
        args = _label_parser().parse_args(argv)
        try:
            if args.command == "label-export":
                count = export_labels(args.input, args.out, args.sample)
                print(f"Exported {count} comment(s) to {args.out}")
            else:
                print_report(report_labels(args.input))
            return 0
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"Instagram labeling error: {exc}", file=sys.stderr)
            return 2
    args = build_parser().parse_args(argv)
    try:
        source_config = _read_json(args.sources, {"usernames": [], "explore": True, "limit_per_source": 20})
        if not isinstance(source_config, dict):
            raise ValueError("sources config must be a JSON object.")
        if args.django_categories:
            try:
                import django
                from django.conf import settings
                if not settings.configured:
                    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
                    django.setup()
                from apps.products.models import Category
                category_queryset = Category.objects.filter(is_active=True).order_by("full_path", "name")
                categories = load_categories([
                    {
                        "id": category.pk,
                        "name": category.name,
                        "full_path": category.full_path or category.get_full_path(),
                        "keywords": category.keywords or [],
                    }
                    for category in category_queryset
                ])
            except Exception as exc:
                raise ValueError(f"Could not load Django categories: {exc}") from exc
        else:
            category_path = args.categories if args.categories.is_file() else Path(__file__).with_name("categories.example.json")
            categories = _category_rows(_read_json(category_path, {"categories": []}))
        sources = _sources(source_config, args)
        limit = args.limit if args.limit is not None else int(source_config.get("limit_per_source", 20))
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        collector = OpenCLICollector(executable=args.opencli_bin, timeout=args.timeout)

        observations = []
        skipped_non_persian = 0
        for source in sources:
            raw_posts = collector.fetch(source, limit)
            owner = source.value if source.kind == "user" else ""
            for post in analyze_posts(raw_posts, {"kind": source.kind, "value": source.value}, categories, owner):
                if not post["caption_is_persian_script"]:
                    skipped_non_persian += 1
                    continue
                post["collected_at"] = datetime.now(timezone.utc).isoformat()
                observations.append(post)
            print(f"{source.kind} {source.value}: {len(raw_posts)} post(s) read", file=sys.stderr)

        # One record per post URL/id when available. Items without a stable key are
        # kept too; they are explicitly marked for review and can be linked later.
        unique: dict[str, dict] = {}
        for index, row in enumerate(observations):
            key = row.get("post_url") or row.get("post_id")
            if key:
                unique[str(key)] = row
            else:
                unique[f"unlinked:{index}:{row.get('seller_username') or 'unknown'}"] = row
        observations = list(unique.values())

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archive = args.output_dir / f"posts-{timestamp}.jsonl"
        review = args.output_dir / f"review-{timestamp}.jsonl"
        candidates = [row for row in observations if row["is_product_ad_candidate"]]
        review_queue = [row for row in observations if row["is_product_ad_candidate"] and not row["ready_for_review"]]
        _write_jsonl(archive, observations)
        _write_jsonl(review, candidates)
        if review_queue:
            _write_jsonl(args.output_dir / f"needs-review-{timestamp}.jsonl", review_queue)

        print(f"Read {len(observations)} Persian-caption post(s); skipped {skipped_non_persian} non-Persian or mixed-language post(s).")
        print(f"Product-ad candidates: {len(candidates)}; flagged for extra review: {len(review_queue)}.")
        print(f"Archive: {archive}")
        print(f"Candidate review queue: {review}")
        print("This step only reads posts. It does not read comments, create products, or write to the main Django database.")
        return 0
    except (CollectorError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Instagram discovery error: {exc}", file=sys.stderr)
        return 2
