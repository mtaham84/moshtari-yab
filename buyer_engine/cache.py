from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from .core import ProductProfile, normalize_text


class DiscoveryCache:
    def __init__(self, path: str | Path | None = None, ttl_seconds: int | None = None):
        self.path = Path(path or os.getenv("DISCOVERY_CACHE_PATH", "output/discovery_cache.sqlite3"))
        self.ttl_seconds = max(0, ttl_seconds if ttl_seconds is not None else int(os.getenv("VOCABULARY_CACHE_TTL", "604800")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS discovery_cache (cache_key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at REAL NOT NULL)")
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA journal_mode=DELETE")
        return connection

    @staticmethod
    def _key(profile: ProductProfile, language: str = "fa", context: dict[str, Any] | None = None) -> str:
        context = {
            "product": normalize_text(profile.product_name),
            "category": normalize_text(profile.category),
            "attributes": profile.attributes,
            "city": normalize_text(profile.city or ""),
            "quantity": profile.quantity,
            "transaction": profile.transaction_type,
            "language": language,
            "context": context or {},
        }
        payload = json.dumps(context, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, profile: ProductProfile, language: str = "fa", context: dict[str, Any] | None = None) -> dict[str, Any] | None:
        key = self._key(profile, language, context)
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT value, updated_at FROM discovery_cache WHERE cache_key = ?", (key,)).fetchone()
        if not row:
            return None
        if self.ttl_seconds and time.time() - row[1] >= self.ttl_seconds:
            self.delete(profile, language, context)
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            self.delete(profile, language, context)
            return None

    def set(self, profile: ProductProfile, value: dict[str, Any], language: str = "fa", context: dict[str, Any] | None = None) -> None:
        key = self._key(profile, language, context)
        with closing(self._connect()) as connection:
            connection.execute("INSERT INTO discovery_cache(cache_key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, json.dumps(value, ensure_ascii=False), time.time()))
            connection.commit()

    def delete(self, profile: ProductProfile, language: str = "fa", context: dict[str, Any] | None = None) -> None:
        with closing(self._connect()) as connection:
            connection.execute("DELETE FROM discovery_cache WHERE cache_key = ?", (self._key(profile, language, context),))
            connection.commit()
