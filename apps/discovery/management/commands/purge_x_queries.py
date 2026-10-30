"""Deletes every stored X search query that is longer than three words (X operators and -negatives are not counted).

Covers the collector's SQLite state (bandit + ProQE), the Postgres ``collector_query_runs`` table and the automatic
rows of ``XSearchQuery``. Seller-written (manual) queries are kept. New, short queries are generated on the next cycle.
"""
import os
import re

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

from workers.x_collector.intent_queries import positive_word_count


def purge_legacy_term_cache(dry_run: bool = True) -> int | None:
    """Count/delete cached per-product X terms while retaining family-keyed entries."""
    schema = getattr(settings, "NEED_ENGINE_SCHEMA", "need_engine")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        return None
    try:
        with connection.cursor() as cur:
            cur.execute(f'SELECT k FROM "{schema}".kv WHERE k LIKE %s', ["x_qterms:%"])
            keys = [r[0] for r in cur.fetchall() if not r[0][len("x_qterms:"):].startswith("f")]
            if keys and not dry_run:
                cur.execute(f'DELETE FROM "{schema}".kv WHERE k = ANY(%s)', [keys])
            return len(keys)
    except Exception:
        return None


class Command(BaseCommand):
    help = "Remove outdated X queries and per-product learning state from all stores."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--max-words", type=int, default=3)

    def handle(self, *args, **opts):
        limit, dry = opts["max_words"], opts["dry_run"]
        total = {}

        # 1. SQLite state of the X collector (bandit counters + ProQE queries)
        state = os.environ.get("X_COLLECT_STATE_FILE", "output/x_collector_state.sqlite3")
        if os.path.exists(state):
            from workers.x_collector.proqe import purge_legacy_product_state, purge_long_queries
            if dry:
                import sqlite3
                with sqlite3.connect(state) as db:
                    n = 0
                    for table in ("query_runs", "proqe_expanded_queries", "proqe_query_outcomes"):
                        try:
                            n += sum(1 for (q,) in db.execute(f"SELECT DISTINCT query FROM {table}") if positive_word_count(q) > limit)
                        except sqlite3.OperationalError:
                            pass
                total["sqlite"] = n
            else:
                total["sqlite"] = purge_long_queries(state, limit)
            total["legacy_family_state"] = purge_legacy_product_state(state, dry_run=dry)

        # 2. Postgres collector state
        with connection.cursor() as cur:
            cur.execute("SELECT table_schema FROM information_schema.tables WHERE table_name = 'collector_query_runs'")
            for (schema,) in cur.fetchall():
                cur.execute(f'SELECT DISTINCT query FROM "{schema}".collector_query_runs WHERE platform = %s', ["x"])
                doomed = [q for (q,) in cur.fetchall() if positive_word_count(q) > limit]
                if doomed and not dry:
                    cur.execute(f'DELETE FROM "{schema}".collector_query_runs WHERE platform = %s AND query = ANY(%s)', ["x", doomed])
                total[f"{schema}.collector_query_runs"] = len(doomed)

        # 3. Automatic Django rows (seller manual queries stay)
        from apps.discovery.models import XSearchQuery
        qs = XSearchQuery.objects.exclude(kind="manual")
        ids = [r.pk for r in qs if positive_word_count(r.text) > limit]
        if ids and not dry:
            XSearchQuery.objects.filter(pk__in=ids).delete()
        total["XSearchQuery(auto)"] = len(ids)

        # 4. LLM query cache. Per-product keys are numeric/legacy; family keys always start with `f`.
        cache_count = purge_legacy_term_cache(dry_run=dry)
        total["legacy_llm_term_cache"] = cache_count if cache_count is not None else "unavailable"

        self.stdout.write(self.style.SUCCESS(f"{'would delete' if dry else 'deleted'}: {total}"))
