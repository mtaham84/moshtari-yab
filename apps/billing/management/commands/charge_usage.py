"""Charge sellers' wallets for new LLM costs (also done automatically by ``sync_opportunities --follow``).

    python manage.py charge_usage
    python manage.py charge_usage --follow --interval 30
"""
import time

from django.core.management.base import BaseCommand

from apps.billing.services import charge_pending_usage


class Command(BaseCommand):
    help = "Turn new need_engine cost rows into wallet charges (pay-as-you-go)."

    def add_arguments(self, parser):
        parser.add_argument("--follow", action="store_true")
        parser.add_argument("--interval", type=float, default=30.0)

    def handle(self, *args, **opts):
        while True:
            r = charge_pending_usage()
            if r["rows"]:
                self.stdout.write(f"{r['rows']} cost rows → {r['businesses']} sellers charged {r['toman']:,.0f} toman")
            if not opts["follow"]:
                break
            time.sleep(opts["interval"])
