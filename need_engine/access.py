"""Who may see a need found in a chat — decided BEFORE any LLM call (see apps/discovery/sources.py).

  • an active GLOBAL source for the chat  → every seller's products are matched;
  • only active PRIVATE sources            → only the products of those sellers;
  • no active source                       → the chat is not analysed at all.

The rule is read from the panel's ``discovery_monitoredcommunity`` table (read-only) once per engine run.
``NE_SOURCE_ACCESS=open`` (tests, offline demo, crawler used without the panel) disables the rule.
"""
from __future__ import annotations

import logging
from typing import Any

from need_engine.config import EngineConfig

log = logging.getLogger("need_engine.access")
ALL = "*"   # marker: every seller


class SourceAccess:
    def __init__(self, cfg: EngineConfig, db: Any = None):
        self.cfg = cfg
        self.enabled = cfg.source_access != "open" and cfg.messages_source == "db"
        self._db = db
        self.rules: dict[str, Any] = {}       # chat_id → ALL | frozenset(business_id)
        self.loaded = False

    def _conn(self):
        if self._db is None:
            from need_engine.sources import _DB
            self._db = _DB(self.cfg.database_url)
        return self._db

    def refresh(self) -> None:
        if not self.enabled:
            return
        from need_engine.sources import _ident

        db = self._conn()
        table = _ident(self.cfg.communities_table)
        exists = db.all("SELECT to_regclass(%s) AS t", (table,))
        if not exists or not exists[0]["t"]:
            if not self.loaded:
                log.warning("%s not found: source access rule disabled until the panel is migrated", table)
            self.rules, self.loaded = {}, False
            return
        rows = db.all(f"""SELECT telegram_chat_id, scope, business_id FROM {table}
                          WHERE is_active AND telegram_chat_id IS NOT NULL""")
        rules: dict[str, Any] = {}
        for r in rows:
            key = str(r["telegram_chat_id"])
            if r["scope"] == "GLOBAL" or r["business_id"] is None:
                rules[key] = ALL
            elif rules.get(key) != ALL:
                rules[key] = frozenset(rules.get(key, frozenset()) | {str(r["business_id"])})
        self.rules, self.loaded = rules, True

    # ── queries ─────────────────────────────────────────────────────────────
    def _active(self) -> bool:
        return self.enabled and self.loaded

    def analysed(self, chat_id: str) -> bool:
        return not self._active() or str(chat_id) in self.rules

    def sellers(self, chat_id: str) -> frozenset[str] | None:
        """None = every seller; otherwise the business ids whose products may be matched."""
        if not self._active():
            return None
        rule = self.rules.get(str(chat_id), frozenset())
        return None if rule == ALL else rule

    def allows(self, chat_id: str, business_id: str | None) -> bool:
        allowed = self.sellers(chat_id)
        return allowed is None or (business_id is not None and str(business_id) in allowed)

    def owners(self, chat_id: str) -> list[str]:
        """Sellers that pay for analysing this chat (empty for global/platform chats)."""
        allowed = self.sellers(chat_id)
        return sorted(allowed) if allowed else []
