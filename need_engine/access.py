"""Who may see a need found in a chat — decided BEFORE any LLM call (see apps/discovery/sources.py).

  • an active GLOBAL source for the chat  → every seller's products are matched;
  • only active PRIVATE sources            → only the products of those sellers;
  (a chat is analysed once however many sources point to it; who pays for it: ``owners``)
  • no active source                       → the chat is not analysed at all.

The rule is read from the panel's ``discovery_monitoredcommunity`` table (read-only) once per engine run.
``NE_SOURCE_ACCESS=open`` (tests, offline demo, crawler used without the panel) disables the rule.

Pay-as-you-go: sellers whose prepaid balance is used up (``ModelRegistry.blocked``) are left out — their products are
not matched and they pay for nothing. Their work is HELD, not lost («در انتظار پرداخت», see engine.py): messages of a
private chat whose owners are all blocked are still collected but not analysed (``held``), needs that the seller's
products could have matched are queued per seller, and new/changed products wait; all of it resumes after a top-up.
"""
from __future__ import annotations

import logging
from typing import Any

from need_engine.config import EngineConfig

log = logging.getLogger("need_engine.access")
ALL = "*"   # marker: every seller
X_CHAT = "x:public"   # chat_id of every X post (sources.XMessageSource)
X_PRODUCT_PREFIX = "x:p:"   # per-product X chats (sources.XProductHitSource)


class SourceAccess:
    def __init__(self, cfg: EngineConfig, db: Any = None, registry: Any = None):
        self.cfg, self.registry = cfg, registry
        self.blocked: frozenset[str] = frozenset()
        self.enabled = cfg.source_access != "open" and cfg.messages_source == "db"
        self._db = db
        self.rules: dict[str, Any] = {}       # chat_id → ALL | frozenset(business_id)
        self.private: dict[str, frozenset[str]] = {}   # chat_id → sellers watching it privately (also on global chats)
        self.loaded = False

    def _conn(self):
        if self._db is None:
            from need_engine.sources import _DB
            self._db = _DB(self.cfg.database_url)
        return self._db

    def refresh(self) -> None:
        if self.registry is not None:
            self.blocked = self.registry.blocked()
        if not self.enabled:
            return
        from need_engine.sources import _ident

        db = self._conn()
        table = _ident(self.cfg.communities_table)
        exists = db.all("SELECT to_regclass(%s) AS t", (table,))
        if not exists or not exists[0]["t"]:
            if not self.loaded:
                log.warning("%s not found: source access rule disabled until the panel is migrated", table)
            self.rules, self.private, self.loaded = {}, {}, False
            return
        rows = db.all(f"""SELECT telegram_chat_id, scope, business_id FROM {table}
                          WHERE is_active AND telegram_chat_id IS NOT NULL""")
        rules: dict[str, Any] = {}
        private: dict[str, frozenset[str]] = {}
        for r in rows:
            key = str(r["telegram_chat_id"])
            if r["scope"] == "GLOBAL" or r["business_id"] is None:
                rules[key] = ALL
                continue
            private[key] = private.get(key, frozenset()) | {str(r["business_id"])}
            if rules.get(key) != ALL:
                rules[key] = private[key]
        if self.cfg.x_enabled and self.cfg.x_mode in {"public", "both"}:   # public X posts: platform source, platform pays
            rules[X_CHAT] = ALL
        rules.update({k: v for k, v in self.rules.items() if k.startswith(X_PRODUCT_PREFIX)})   # kept across refreshes
        private.update({k: v for k, v in self.private.items() if k.startswith(X_PRODUCT_PREFIX)})
        self.rules, self.private, self.loaded = rules, private, True

    def set_x_products(self, owners: dict[str, str | None]) -> None:
        """Per-product X mode: chat ``x:p:<product>`` belongs to the product's seller only — only that product is
        matched, the seller pays every call, and the posts wait («در انتظار پرداخت») while the balance is used up."""
        self.rules = {k: v for k, v in self.rules.items() if not k.startswith(X_PRODUCT_PREFIX)}
        self.private = {k: v for k, v in self.private.items() if not k.startswith(X_PRODUCT_PREFIX)}
        for pid, bid in owners.items():
            key = X_PRODUCT_PREFIX + str(pid)
            if bid:
                self.rules[key] = self.private[key] = frozenset({str(bid)})
            else:
                self.rules[key] = ALL

    # ── queries ─────────────────────────────────────────────────────────────
    def _active(self) -> bool:
        return self.enabled and self.loaded

    def monitored(self, chat_id: str) -> bool:
        """An active source exists for the chat → its messages are collected (even while its owners are blocked)."""
        return not self._active() or str(chat_id) in self.rules

    def held(self, chat_id: str) -> bool:
        """Collected but waiting for payment: a private chat whose owners are all blocked."""
        return self.monitored(chat_id) and not self.analysed(chat_id)

    def analysed(self, chat_id: str) -> bool:
        if not self._active():
            return True
        rule = self.rules.get(str(chat_id))
        if rule is None:
            return False
        return rule == ALL or bool(rule - self.blocked)

    def sellers(self, chat_id: str) -> frozenset[str] | None:
        """None = every seller; otherwise the business ids whose products may be matched."""
        if not self._active():
            return None
        rule = self.rules.get(str(chat_id), frozenset())
        return None if rule == ALL else rule

    def allows(self, chat_id: str, business_id: str | None) -> bool:
        if business_id is not None and str(business_id) in self.blocked:
            return False
        allowed = self.sellers(chat_id)
        return allowed is None or (business_id is not None and str(business_id) in allowed)

    def owners(self, chat_id: str) -> list[str]:
        """Sellers that pay for analysing this chat: its private watchers with balance — also when the chat is a global
        source too (everyone is still matched there). Each one is billed the whole extraction (NE_CHARGE_EACH_OWNER);
        the chat itself is analysed once. Empty = platform cost."""
        if not self._active():
            return []
        key = str(chat_id)
        rule = self.rules.get(key)
        mine = self.private.get(key, rule if isinstance(rule, frozenset) else frozenset())
        return sorted(mine - self.blocked)
