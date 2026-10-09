"""Import opportunities published by need_engine (``need_engine.opportunities``) into the panel.

    python manage.py sync_opportunities              # import new/changed opportunities once
    python manage.py sync_opportunities --follow     # keep importing while the engine runs
    python manage.py sync_opportunities --from-start # re-import everything (idempotent)

Every round also charges sellers' wallets for new LLM costs (pay-as-you-go, apps/billing) unless --no-billing.
"""
import logging
import time

from django.core.management.base import BaseCommand

from apps.discovery.engine_bridge import import_opportunity, published_after
from apps.billing.services import charge_pending_usage, heartbeat
from apps.discovery.models import EngineSyncCursor

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Import opportunities published by need_engine into the panel database."

    def add_arguments(self, parser):
        parser.add_argument("--follow", action="store_true")
        parser.add_argument("--interval", type=float, default=10.0)
        parser.add_argument("--from-start", action="store_true")
        parser.add_argument("--no-billing", action="store_true", help="do not charge wallets in this process")

    def handle(self, *args, **opts):
        cursor, _ = EngineSyncCursor.objects.get_or_create(name="opportunities")
        if opts["from_start"]:
            cursor.position = 0
        while True:
            n = 0
            while True:
                batch = published_after(cursor.position)
                for seq, payload in batch:
                    import_opportunity(payload)
                    cursor.position = seq
                    n += 1
                if batch:
                    cursor.save(update_fields=["position", "updated_at"])
                if len(batch) < 500:
                    break
            if n:
                self.stdout.write(f"imported {n} opportunity updates")
            if not opts["no_billing"]:
                try:
                    charge_pending_usage()
                except Exception:   # billing must never stop the import
                    log.exception("charging wallets failed")
            heartbeat("sync")
            if not opts["follow"]:
                break
            time.sleep(opts["interval"])
