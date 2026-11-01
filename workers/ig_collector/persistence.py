"""
Persistence manager for Instagram collector (P08.11 / R39, R41).

Extracts file-locking, JSONL appending, offset rollback, and state store
synchronization into a dedicated, testable repository component.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from file_lock import exclusive_file_lock
from shared.collector_kit.jsonl import append_line, write_latest
from shared.collector_kit.status import write_status
from shared.records import SourceRecord, to_json_line

log = logging.getLogger("ig_collector.persistence")


@dataclass(frozen=True)
class PersistenceResult:
    """
    Structured outcome of a collector persistence operation.
    """
    success: bool
    records_persisted: int = 0
    seller_leads_persisted: int = 0
    review_queue_persisted: int = 0
    comment_evaluations_persisted: int = 0
    error: Optional[str] = None


class CollectorPersistenceManager:
    """
    Manages atomic writing of collected records, seller leads, human review queue,
    and comment evaluations with offset-based rollback and idempotent deduplication.
    """

    def __init__(self, output_dir: Path, store: Any = None):
        self.output_dir = output_dir
        self.store = store

    def set_store(self, store: Any) -> None:
        self.store = store

    def persist_records(self, rows: List[Dict[str, Any]], dated: Path) -> int:
        """
        Persist collected post/comment records to dated and latest JSONL files atomically.
        Rolls back via file truncation if store synchronization fails.
        """
        if not rows:
            return 0
        self.output_dir.mkdir(parents=True, exist_ok=True)
        latest = self.output_dir / "latest.jsonl"
        paths = sorted({latest.resolve(), dated.resolve()}, key=str)
        locks = [path.with_suffix(path.suffix + ".lock") for path in paths]

        with exclusive_file_lock(locks[0]):
            with exclusive_file_lock(locks[1]):
                offsets = {path: path.stat().st_size if path.exists() else 0 for path in paths}
                try:
                    content = "".join(to_json_line(SourceRecord.from_dict(row)) + "\n" for row in rows)
                    write_latest(latest, content, append=True, locked=True)
                    write_latest(dated, content, append=True, locked=True)
                    if self.store is not None:
                        self.store.add_many(rows)
                    return len(rows)
                except Exception:
                    for path, offset in offsets.items():
                        if path.exists():
                            with path.open("r+b") as stream:
                                stream.truncate(offset)
                    raise

    def persist_seller_leads(self, rows: List[Dict[str, Any]]) -> int:
        """
        Persist seller leads to seller_leads.jsonl idempotently.
        """
        if not rows:
            return 0
        target = self.output_dir / "seller_leads.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        assert self.store is not None, "State store must be initialized to persist seller leads"

        with exclusive_file_lock(target.with_suffix(target.suffix + ".lock")):
            existing: Set[str] = set()
            if target.exists():
                for line in target.read_text(encoding="utf-8").splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("id"):
                        existing.add(str(item["id"]))
            new_rows = [row for row in rows if row["id"] not in existing]
            content = "".join(
                to_json_line(SourceRecord.from_dict(row)) + "\n"
                for row in new_rows
            )
            if content:
                offset = write_latest(target, content, append=True, locked=True)
                try:
                    state_rows = [{**row, "id": f"seller_lead:{row['id']}", "kind": "seller_lead"}
                                  for row in new_rows]
                    self.store.add_many(state_rows)
                except Exception:
                    with target.open("r+b") as stream:
                        stream.truncate(offset)
                    raise
            return len(new_rows)

    def persist_review_queue(self, rows: List[Dict[str, Any]]) -> int:
        """
        Persist human review items to needs-review.jsonl deduplicated by ID and audit flag.
        """
        if not rows:
            return 0
        target = self.output_dir / "needs-review.jsonl"
        existing: Set[Tuple[str, bool]] = set()
        if target.exists():
            for line in target.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("id"):
                    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                    existing.add((str(item["id"]), bool(metadata.get("audit"))))

        new_rows = []
        for row in rows:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            key = (str(row.get("id") or ""), bool(metadata.get("audit")))
            if key[0] and key not in existing:
                new_rows.append(row)
                existing.add(key)

        if new_rows:
            queued_at = datetime.now(timezone.utc).isoformat()
            queued_rows = []
            for row in new_rows:
                queued = dict(row)
                metadata = dict(queued.get("metadata") or {})
                metadata.setdefault("review_queued_at", queued.pop("review_queued_at", queued_at))
                queued["metadata"] = metadata
                queued_rows.append(queued)
            content = "".join(to_json_line(SourceRecord.from_dict(row)) + "\n" for row in queued_rows)
            write_latest(target, content, append=True)
        return len(new_rows)

    def persist_comment_evaluations(self, rows: List[Dict[str, Any]]) -> int:
        """
        Persist evaluation logs to comment-evaluations.jsonl deduplicated by comment ID.
        """
        if not rows:
            return 0
        target = self.output_dir / "comment-evaluations.jsonl"
        existing: Set[str] = set()
        if target.exists():
            for line in target.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("id"):
                    existing.add(str(item["id"]))
        new_rows = [row for row in rows if row["id"] not in existing]
        if new_rows:
            content = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                              for row in new_rows)
            write_latest(target, content, append=True)
        return len(new_rows)

    def write_rejected(self, rows: List[Dict[str, Any]], target: Path) -> None:
        """
        Append rejected summaries without sensitive text or author IDs.
        """
        if not rows:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        content = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
        append_line(target, content)

    def write_status(self, result: Dict[str, Any], daily_cap: int) -> None:
        """
        Write cycle outcome and operational status to status.json.
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / "status.json"
        payload = {**result, "daily_cap": daily_cap, "updated_at": datetime.now(timezone.utc).isoformat()}
        if payload.get("output"):
            payload["output"] = Path(payload["output"]).name
        write_status(target, payload)
