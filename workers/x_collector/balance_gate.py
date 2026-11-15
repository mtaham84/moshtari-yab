"""Read-only, fail-closed wallet checks shared by X query sources and the worker."""
import logging

import psycopg

from need_engine.config import EngineConfig
from need_engine.registry import ModelRegistry
from .config import seller_allowlist

log = logging.getLogger('x_collector.queries')


class BalanceGate:
    def __init__(self, config=None, db=None, registry=None):
        self.registry = registry if registry is not None else ModelRegistry(config or EngineConfig(), db=db)
        self.allowlist = seller_allowlist()
        self.paused_businesses: set[str] = set()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.registry.close()

    def funded(self, business_ids) -> set[str]:
        owners = {str(value) for value in business_ids if value is not None and str(value)}
        if not owners:
            return set()
        try:
            blocked = self.registry.blocked(force=True)
        except (RuntimeError, psycopg.Error, ValueError):
            log.error('Seller balances unavailable; refusing X queries', exc_info=True)
            self.paused_businesses.update(owners)
            return set()
        eligible = owners - set(blocked)
        if self.allowlist:
            eligible &= self.allowlist
        self.paused_businesses.update(owners - eligible)
        return eligible
