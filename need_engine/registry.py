"""Providers, models, prices and wallets managed in the admin panel (``/ops/``), read-only by the engine.

Django tables (app ``apps.billing``):
  • ``billing_provider``         — OpenAI-compatible endpoint: base_url + api_key
  • ``billing_aimodel``          — model id, USD price per 1M tokens, rate limits, roles (extract / verify / reply)
  • ``billing_billingsettings``  — USD→Toman rate, markup, balance enforcement
  • ``billing_wallet``           — prepaid balance (Toman) of every seller

Everything falls back to the NE_* environment settings when the tables are missing or empty, so the engine still runs
without the panel (tests, offline demo). The snapshot is reloaded at most every ``NE_REGISTRY_TTL`` seconds, so a
model added/disabled in the panel is used by the running engine within a minute — no restart needed.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from need_engine.config import EngineConfig
from need_engine.crypto import decrypt_secret

log = logging.getLogger("need_engine.registry")

ROLES = ("extract", "verify", "reply")


def role_of(stage: str) -> str | None:
    """Pipeline stage → model role chosen in the admin panel (None = keep the caller's model)."""
    s = (stage or "").lower()
    if s.startswith("embed"):
        return None
    if "verify" in s:
        return "verify"
    if s.startswith("reply") or "rewrite" in s:
        return "reply"
    if s in ("need_extraction", "product_cards", "product_extract") or "extract" in s or "expand" in s:
        return "extract"
    return None


@dataclass
class ModelEntry:
    name: str
    base_url: str
    api_key: str
    price_in: float
    price_out: float
    roles: frozenset[str]
    priority: int
    rpm: int | None = None
    tpm: int | None = None
    rpd: int | None = None
    provider: str = "default"
    deployment_id: str | None = None

    @property
    def identity(self) -> str:
        if self.deployment_id:
            return f"{self.provider}:{self.name}:{self.deployment_id}".lower()
        return f"{self.provider}:{self.name}".lower()

    @property
    def model_ref(self):
        from apps.core.gateway_contract import ModelRef
        return ModelRef(provider_name=self.provider, model_name=self.name, deployment_id=self.deployment_id)


@dataclass
class Snapshot:
    version: int = 1
    created_at: float = field(default_factory=time.time)
    models: dict[str, ModelEntry | None] = field(default_factory=dict)  # identity -> entry OR name -> entry (None if ambiguous)
    by_identity: dict[str, ModelEntry] = field(default_factory=dict)    # provider:name -> entry
    by_role: dict[str, str] = field(default_factory=dict)               # role → primary model identifier
    models_by_role: dict[str, list[str]] = field(default_factory=dict) # role → ordered candidate identifiers
    usd_to_toman: float | None = None
    enforce_balance: bool = False
    min_balance: float = 0.0
    balances: dict[str, float] = field(default_factory=dict)          # business id → Toman
    wallets_loaded: bool = False


class ModelRegistry:
    def __init__(self, cfg: EngineConfig, db: Any = None):
        self.cfg = cfg
        self.enabled = bool(cfg.model_registry) and cfg.products_source == "db"
        self._db = db
        self._version = 1
        self._snap = Snapshot(version=self._version)
        self._loaded_at = 0.0
        self._lock = threading.RLock()

    # ── loading ─────────────────────────────────────────────────────────────
    def _conn(self):
        if self._db is None:
            from need_engine.sources import _DB
            self._db = _DB(self.cfg.database_url)
        return self._db

    def _exists(self, table: str) -> bool:
        rows = self._conn().all("SELECT to_regclass(%s) AS t", (table,))
        return bool(rows and rows[0]["t"])

    def refresh(self, force: bool = False) -> Snapshot:
        if not self.enabled:
            if self.cfg.products_source == "db" and (force or time.time() - self._loaded_at >= self.cfg.registry_ttl):
                with self._lock:
                    if force or time.time() - self._loaded_at >= self.cfg.registry_ttl:
                        self._loaded_at = time.time()
                        try:
                            self._load_billing(self._snap)
                        except Exception as e:
                            log.warning("wallet registry not loaded (%s)", e)
            return self._snap
        with self._lock:
            if not force and time.time() - self._loaded_at < self.cfg.registry_ttl:
                return self._snap
            self._loaded_at = time.time()
            try:
                self._snap = self._load()
            except Exception as e:   # never stop the engine because of the panel
                log.warning("model registry not loaded (%s); using NE_* settings", e)
            return self._snap

    def _load(self) -> Snapshot:
        from need_engine.sources import _ident

        self._version += 1
        snap = Snapshot(version=self._version)
        db = self._conn()
        models_t, prov_t = _ident(self.cfg.models_table), _ident(self.cfg.providers_table)
        if self._exists(models_t) and self._exists(prov_t):
            rows = db.all(f"""SELECT p.name AS provider_name, m.name, p.base_url, p.api_key, m.input_price_usd, m.output_price_usd, m.priority,
                                     m.use_extract, m.use_verify, m.use_reply, m.rpm, m.tpm, m.rpd
                              FROM {models_t} m JOIN {prov_t} p ON p.id = m.provider_id
                              WHERE m.is_active AND p.is_active ORDER BY m.priority, m.id""")
            for r in rows:
                roles = frozenset(k for k, on in (("extract", r["use_extract"]), ("verify", r["use_verify"]),
                                                  ("reply", r["use_reply"])) if on)
                prov = (r.get("provider_name") or r.get("provider") or "default").strip()
                e = ModelEntry(
                    name=r["name"],
                    base_url=r["base_url"] or "",
                    api_key=decrypt_secret(r["api_key"]),
                    price_in=float(r["input_price_usd"] or 0),
                    price_out=float(r["output_price_usd"] or 0),
                    roles=roles,
                    priority=int(r["priority"] or 0),
                    rpm=r["rpm"],
                    tpm=r["tpm"],
                    rpd=r["rpd"],
                    provider=prov,
                )
                ident = e.identity
                snap.by_identity[ident] = e
                snap.models[ident] = e

                # Disambiguation logic for plain model name:
                if e.name not in snap.models:
                    snap.models[e.name] = e
                else:
                    existing = snap.models[e.name]
                    if existing is not None and existing.identity != ident:
                        # Ambiguous: multiple providers define this model name
                        snap.models[e.name] = None

                chosen_key = e.name if prov == "default" else ident
                for role in roles:
                    snap.by_role.setdefault(role, chosen_key)
                    if role not in snap.models_by_role:
                        snap.models_by_role[role] = []
                    if chosen_key not in snap.models_by_role[role]:
                        snap.models_by_role[role].append(chosen_key)
        self._load_billing(snap)
        return snap

    def _load_billing(self, snap: Snapshot) -> None:
        from need_engine.sources import _ident

        db = self._conn()
        settings_t = _ident(self.cfg.billing_settings_table)
        if self._exists(settings_t):
            rows = db.all(f"SELECT usd_to_toman, enforce_balance, min_balance_toman FROM {settings_t} ORDER BY id LIMIT 1")
            if rows:
                snap.usd_to_toman = float(rows[0]["usd_to_toman"]) if rows[0]["usd_to_toman"] else None
                snap.enforce_balance = bool(rows[0]["enforce_balance"])
                snap.min_balance = float(rows[0]["min_balance_toman"] or 0)
        wallets_t = _ident(self.cfg.wallets_table)
        if self._exists(wallets_t):
            snap.balances = {str(r["business_id"]): float(r["balance_toman"])
                             for r in db.all(f"SELECT business_id, balance_toman FROM {wallets_t}")}
            snap.wallets_loaded = True

    # ── queries ─────────────────────────────────────────────────────────────
    def freeze_snapshot(self) -> Snapshot:
        """Freezes and returns an immutable snapshot instance for execution duration (P05.02 / R17)."""
        with self._lock:
            snap = self.refresh()
            return Snapshot(
                version=snap.version,
                created_at=snap.created_at,
                models=dict(snap.models),
                by_identity=dict(snap.by_identity),
                by_role=dict(snap.by_role),
                models_by_role={k: list(v) for k, v in snap.models_by_role.items()},
                usd_to_toman=snap.usd_to_toman,
                enforce_balance=snap.enforce_balance,
                min_balance=snap.min_balance,
                balances=dict(snap.balances),
                wallets_loaded=snap.wallets_loaded,
            )

    def model_for(self, stage: str) -> str | None:
        role = role_of(stage)
        return self.refresh().by_role.get(role) if role else None

    def candidate_models_for(self, stage: str) -> list[str]:
        role = role_of(stage)
        if not role:
            return []
        snap = self.refresh()
        return list(snap.models_by_role.get(role, []))

    def model_ref_for(self, stage: str) -> Any | None:
        e = self.entry(self.model_for(stage))
        return e.model_ref if e else None

    def candidate_model_refs_for(self, stage: str) -> list[Any]:
        candidates = self.candidate_models_for(stage)
        refs = []
        for c in candidates:
            e = self.entry(c)
            if e:
                refs.append(e.model_ref)
        return refs

    def entry(self, model: Any) -> ModelEntry | None:
        if not model:
            return None
        if hasattr(model, "identity"):
            key = model.identity
        else:
            key = str(model).strip()

        snap = self.refresh()
        if ":" in key:
            return snap.by_identity.get(key.lower())

        if key in snap.models:
            val = snap.models[key]
            if val is None:
                colliding = [e.identity for e in snap.by_identity.values() if e.name.lower() == key.lower()]
                raise ValueError(
                    f"Ambiguous model name '{key}': matches multiple providers: {colliding}. "
                    f"Specify full 'provider:model' identity."
                )
            return val

        # Case-insensitive check
        matches = [e for e in snap.by_identity.values() if e.name.lower() == key.lower()]
        if len(matches) > 1:
            colliding = [e.identity for e in matches]
            raise ValueError(
                f"Ambiguous model name '{key}': matches multiple providers: {colliding}. "
                f"Specify full 'provider:model' identity."
            )
        if len(matches) == 1:
            return matches[0]
        return None

    def endpoint(self, model: str) -> tuple[str, str]:
        """(base_url, api_key) for a model: its provider in the panel, else NE_LLM_BASE_URL / NE_LLM_API_KEY.
        A provider saved without a key uses NE_LLM_API_KEY (e.g. the one imported from .env)."""
        e = self.entry(model)
        if e and e.base_url:
            return e.base_url, e.api_key or self.cfg.llm_api_key
        return self.cfg.llm_base_url, self.cfg.llm_api_key

    def price(self, model: str) -> tuple[float, float]:
        e = self.entry(model)
        if e is not None:
            return e.price_in, e.price_out
        p = self.cfg.prices.get(model) or (0.0, 0.0)
        return float(p[0]), float(p[1])

    def rate_limit(self, model: str) -> dict | None:
        e = self.entry(model)
        if e is None or (e.rpm is None and e.tpm is None and e.rpd is None):
            return None
        return {"rpm": e.rpm, "tpm": e.tpm, "rpd": e.rpd}

    def usd_to_toman(self) -> float:
        return self.refresh().usd_to_toman or self.cfg.usd_to_toman

    def has_credentials(self) -> bool:
        snap = self.refresh()
        return bool(self.cfg.llm_api_key) or any(e.api_key for e in snap.models.values())

    def blocked(self, force: bool = False) -> frozenset[str]:
        """Sellers whose prepaid balance is used up (pay-as-you-go): their products are not matched and their
        private chats are not analysed until the balance is topped up.
        Sellers without a wallet row are not blocked (the panel creates one for every business)."""
        snap = self.refresh(force=force)
        if self.cfg.products_source == "db" and not snap.wallets_loaded:
            raise RuntimeError("wallet balances unavailable; refusing paid discovery work")
        return frozenset(b for b, bal in snap.balances.items()
                         if bal <= 0 or (snap.enforce_balance and bal <= snap.min_balance))

    def is_blocked(self, business_id: str | None) -> bool:
        return business_id is not None and str(business_id) in self.blocked()
