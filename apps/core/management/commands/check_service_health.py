import sys
import time
from pathlib import Path
from django.core.management.base import BaseCommand
from apps.discovery.models import EngineSyncCursor


class Command(BaseCommand):
    help = "Health probe for background sync and worker services (P09.05)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--service",
            type=str,
            required=True,
            help="Name of the service heartbeat to inspect (e.g. sync, sync_x).",
        )
        parser.add_argument(
            "--max-age",
            type=int,
            default=120,
            help="Maximum acceptable age of heartbeat in seconds. Default: 120",
        )

    def handle(self, *args, **options):
        service = options["service"]
        max_age = options["max_age"]
        now_ts = int(time.time())

        last_ts = None

        # 1. Check database cursor heartbeat
        cursor = EngineSyncCursor.objects.filter(name=f"heartbeat:{service}").first()
        if cursor and cursor.position:
            last_ts = cursor.position

        # 2. Check local file heartbeat if db cursor not recent or unavailable
        heartbeat_file = Path(f"/tmp/heartbeat_{service}.txt")
        if heartbeat_file.exists():
            try:
                file_ts = int(heartbeat_file.read_text(encoding="utf-8").strip())
                if last_ts is None or file_ts > last_ts:
                    last_ts = file_ts
            except Exception:
                pass

        if last_ts is None:
            self.stderr.write(f"UNHEALTHY: No heartbeat record found for service '{service}'")
            sys.exit(1)

        age = now_ts - last_ts
        if age > max_age:
            self.stderr.write(f"UNHEALTHY: Service '{service}' heartbeat is stale ({age}s > {max_age}s limit)")
            sys.exit(1)

        self.stdout.write(f"HEALTHY: Service '{service}' heartbeat active ({age}s ago <= {max_age}s limit)")
        sys.exit(0)
