import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.discovery.engine_bridge import sync_x_status_file


class Command(BaseCommand):
    help = "Import the sanitized X collector status file."

    def add_arguments(self, parser):
        parser.add_argument("--follow", action="store_true")
        parser.add_argument("--interval", type=int, default=30)

    def handle(self, *args, **options):
        path = settings.BASE_DIR / __import__("os").getenv("X_STATUS_FILE", "data/x_collected/status.json")
        while True:
            sync_x_status_file(str(path), int(__import__("os").getenv("X_STATUS_STALE_MINUTES", "30")))
            if not options["follow"]:
                break
            time.sleep(max(1, options["interval"]))
