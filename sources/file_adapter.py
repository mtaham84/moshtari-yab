"""Load messages from JSONL / CSV files (X dataset, demo data, manual exports)
and enqueue them for analysis.

JSONL: one object per line. Minimal fields: ``id`` and ``text``. Optional:
``source``, ``channel_id``, ``channel_title``, ``author_id``, ``author_handle``,
``author_name``, ``created_at``, ``url``, ``context`` (list of
{"id", "relation": previous|parent|reply, "author_name", "text"}), ``metadata``.

CSV: same flat columns; ``context`` may hold a JSON list.

Usage:
    python -m sources.file_adapter data/sample/messages.jsonl
    python -m sources.file_adapter tweets.csv --source x
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Any, Iterator

from analysis.schemas import Author, ContextMessage, SocialMessage

log = logging.getLogger("sources.file_adapter")


def record_to_message(rec: dict[str, Any], default_source: str = "file") -> SocialMessage:
    source = (rec.get("source") or default_source).strip().lower()
    raw_id = str(rec.get("id") or rec.get("uid") or "").strip()
    if not raw_id:
        raise ValueError("record has no id")
    uid = raw_id if raw_id.startswith(f"{source}:") else f"{source}:{raw_id}"
    context_raw = rec.get("context") or []
    if isinstance(context_raw, str):
        context_raw = json.loads(context_raw) if context_raw.strip() else []
    context = [
        ContextMessage(
            id=str(c.get("id") or f"{raw_id}-ctx{i}"),
            relation=c.get("relation", "previous"),
            author_name=c.get("author_name"),
            text=c.get("text", ""),
            created_at=c.get("created_at"),
        )
        for i, c in enumerate(context_raw)
    ]
    metadata = rec.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata) if metadata.strip() else {}
    return SocialMessage(
        uid=uid,
        source=source if source in {"telegram", "x", "divar", "file"} else "other",
        channel_id=rec.get("channel_id") or None,
        channel_title=rec.get("channel_title") or None,
        text=rec["text"],
        author=Author(
            id=rec.get("author_id") or None,
            handle=rec.get("author_handle") or None,
            display_name=rec.get("author_name") or None,
            is_bot=str(rec.get("author_is_bot", "")).lower() in {"1", "true", "yes"},
        ),
        created_at=rec.get("created_at") or None,
        url=rec.get("url") or None,
        context=context,
        metadata=metadata,
    )


def iter_records(path: str | Path) -> Iterator[dict[str, Any]]:
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as fh:
            yield from csv.DictReader(fh)
        return
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning("%s:%s invalid JSON skipped (%s)", path, line_no, exc)


def load_file(path: str | Path, default_source: str = "file") -> list[SocialMessage]:
    messages = []
    for rec in iter_records(path):
        try:
            messages.append(record_to_message(rec, default_source))
        except (KeyError, ValueError) as exc:
            log.warning("Skipping record %r: %s", rec.get("id"), exc)
    return messages


def main(argv: list[str] | None = None) -> None:
    from analysis.store import AnalysisStore

    parser = argparse.ArgumentParser(description="Enqueue messages from a JSONL/CSV file for analysis.")
    parser.add_argument("path")
    parser.add_argument("--source", default="file", help="telegram | x | divar | file (default: file)")
    parser.add_argument("--db-path", default=None, help="Analysis SQLite path (default: ANALYSIS_DB_PATH)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    messages = load_file(args.path, args.source)
    added = AnalysisStore(args.db_path).enqueue_many(messages)
    print(f"Loaded {len(messages)} messages, enqueued {added} new (duplicates skipped: {len(messages) - added}).")


if __name__ == "__main__":
    main()
