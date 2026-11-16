"""Read-only, fail-closed wallet checks shared by X query sources and the worker."""
import logging

import psycopg

from need_engine.config import EngineConfig
from need_engine.registry import ModelRegistry
from .config import seller_allowlist, require_wallet

log = logging.getLogger('x_collector.queries')


class BalanceGate:
    def __init__(self, config=None, db=None, registry=None, *, allow_unowned=False):
        self.registry = registry if registry is not None else ModelRegistry(config or EngineConfig(), db=db)
        self.allowlist = seller_allowlist()
        self.require_wallet = require_wallet()
        self.allow_unowned = allow_unowned
        self.skipped_queries: set[str] = set()
        self._warned_unowned = False
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
            balances = self.registry.refresh().balances
            missing = owners - set(balances)
            if missing and self.require_wallet:
                log.warning('X sellers missing wallet rows are blocked: %s', ','.join(sorted(missing)))
                blocked = set(blocked) | missing
        except (RuntimeError, psycopg.Error, ValueError):
            log.error('Seller balances unavailable; refusing X queries', exc_info=True)
            self.paused_businesses.update(owners)
            return set()
        eligible = owners - set(blocked)
        if self.allowlist:
            eligible &= self.allowlist
        self.paused_businesses.update(owners - eligible)
        return eligible


    @staticmethod
    def owners(item) -> list[str]:
        if not isinstance(item, dict):
            return []
        values = item.get('business_ids') or [item.get('business_id')]
        return sorted({str(value) for value in values if value is not None and str(value)})

    def _allow(self, item, funded):
        owners = self.owners(item)
        if not owners:
            if self.allow_unowned:
                return item
            if not self._warned_unowned:
                log.warning('Unowned X query blocked; provide business_id or business_ids')
                self._warned_unowned = True
            self.skipped_queries.add(item['query'] if isinstance(item, dict) else item)
            return None
        eligible = sorted(set(owners) & funded)
        if not eligible:
            self.skipped_queries.add(item['query'])
            return None
        result = {**item, 'business_ids': eligible}
        ownership = item.get('product_business_ids') or {}
        if ownership:
            result['product_ids'] = [str(pid) for pid in item.get('product_ids') or []
                                     if str(ownership.get(str(pid))) in eligible]
        return result

    def filter_queries(self, queries):
        funded = self.funded(owner for item in queries for owner in self.owners(item))
        return [allowed for item in queries if (allowed := self._allow(item, funded)) is not None]

    def eligible_query(self, item):
        # Force a read for every attempt (TTL zero), including account rotations and retries.
        return self._allow(item, self.funded(self.owners(item)))

    def stats(self):
        return {'paused_businesses': sorted(self.paused_businesses),
                'skipped_queries_by_balance': len(self.skipped_queries)}
