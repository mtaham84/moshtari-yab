"""Import need_engine output (JSONL) into the panel.

    python manage.py sync_opportunities              # import new lines once
    python manage.py sync_opportunities --follow     # keep importing as the engine writes
    python manage.py sync_opportunities --from-start # re-import the whole file (idempotent)
"""
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.discovery.engine_bridge import import_opportunity, iter_jsonl


class Command(BaseCommand):
    help = "Import opportunities written by need_engine into the panel database."

    def add_arguments(self, parser):
        parser.add_argument("--path", default=settings.NEED_ENGINE_OUTPUT_PATH)
        parser.add_argument("--follow", action="store_true")
        parser.add_argument("--interval", type=float, default=10.0)
        parser.add_argument("--from-start", action="store_true")

    def handle(self, *args, **opts):
        path = Path(opts["path"])
        cursor = path.with_name(path.name + ".offset")
        offset = 0 if opts["from_start"] or not cursor.exists() else int(cursor.read_text() or 0)
        while True:
            if path.exists() and path.stat().st_size < offset:  # file was rotated/recreated
                offset = 0
            n = 0
            if path.exists():
                for offset, payload in iter_jsonl(path, offset):
                    import_opportunity(payload)
                    cursor.write_text(str(offset))
                    n += 1
            if n:
                self.stdout.write(f"imported {n} opportunity updates")
            if not opts["follow"]:
                break
            time.sleep(opts["interval"])
