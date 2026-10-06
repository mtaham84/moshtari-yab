from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.getenv("JOB_STORE_PATH", "output/jobs.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("CREATE TABLE IF NOT EXISTS discovery_jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, phase TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL, stop_requested INTEGER NOT NULL DEFAULT 0)")
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def create(self, payload: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex[:12]
        now = utc_now()
        with closing(self._connect()) as connection:
            connection.execute("INSERT INTO discovery_jobs(job_id,status,phase,created_at,updated_at,payload) VALUES(?,?,?,?,?,?)", (job_id, "PENDING", "PENDING", now, now, json.dumps(payload, ensure_ascii=False)))
            connection.commit()
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT job_id,status,phase,created_at,updated_at,payload,stop_requested FROM discovery_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return None
        payload = json.loads(row[5])
        result = payload.get("result", {})
        return {**payload, **result, "job_id": row[0], "status": row[1], "current_phase": row[2], "created_at": row[3], "updated_at": row[4], "stop_requested": bool(row[6])}

    def update(self, job_id: str, status: str | None = None, phase: str | None = None, **values: Any) -> None:
        current = self.get(job_id)
        if current is None:
            raise KeyError(f"Unknown job: {job_id}")
        payload = {key: value for key, value in current.items() if key not in {"job_id", "status", "current_phase", "created_at", "updated_at", "stop_requested"}}
        payload.update(values)
        with closing(self._connect()) as connection:
            connection.execute("UPDATE discovery_jobs SET status=?,phase=?,updated_at=?,payload=? WHERE job_id=?", (status or current["status"], phase or current["current_phase"], utc_now(), json.dumps(payload, ensure_ascii=False), job_id))
            connection.commit()

    def request_stop(self, job_id: str) -> bool:
        with closing(self._connect()) as connection:
            cursor = connection.execute("UPDATE discovery_jobs SET stop_requested=1,status='STOP_REQUESTED',updated_at=? WHERE job_id=? AND status IN ('PENDING','RUNNING','PARTIAL_SUCCESS','PAUSED','STOP_REQUESTED')", (utc_now(), job_id))
            connection.commit()
        return cursor.rowcount > 0

    def clear_stop(self, job_id: str) -> None:
        with closing(self._connect()) as connection:
            connection.execute("UPDATE discovery_jobs SET stop_requested=0 WHERE job_id=?", (job_id,))
            connection.commit()

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute("SELECT job_id,status,phase,created_at,updated_at,payload FROM discovery_jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
        jobs = []
        for row in rows:
            payload = json.loads(row[5])
            result = payload.get("result", {})
            jobs.append({"job_id": row[0], "status": row[1], "current_phase": row[2], "created_at": row[3], "updated_at": row[4], "product": payload.get("profile", {}).get("product_name"), "records_collected": result.get("records_collected", len(payload.get("records", []))), "buyers_found": result.get("buyers_found", 0)})
        return jobs

    def stop_requested(self, job_id: str) -> bool:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT stop_requested FROM discovery_jobs WHERE job_id=?", (job_id,)).fetchone()
        return bool(row and row[0])
