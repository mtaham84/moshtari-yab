from datetime import timedelta
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from psycopg.conninfo import make_conninfo
from file_lock import exclusive_file_lock

from need_engine.retention import (purge_retained_data, purge_panel_x_posts, purge_x_collector_state,
                                   scrub_panel_opportunities)


class Command(BaseCommand):
    help = "Report or purge archived source data older than DATA_RETENTION_DAYS."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, help="Retention days (defaults to DATA_RETENTION_DAYS, currently 180).")
        parser.add_argument("--delete", action="store_true", help="Perform the purge. Without this flag only report counts.")

    def handle(self, *args, **options):
        days = options["days"] if options["days"] is not None else settings.DATA_RETENTION_DAYS
        if days < 1:
            raise CommandError("Retention must be at least one day.")
        db = settings.DATABASES["default"]
        dsn = make_conninfo(**{
            "host": db.get("HOST"), "port": str(db.get("PORT") or ""), "dbname": db.get("NAME"),
            "user": db.get("USER"), "password": db.get("PASSWORD"),
        })
        cutoff = timezone.now() - timedelta(days=days)
        configured_output = Path(os.getenv("X_COLLECT_OUTPUT_DIR", "data/x_collected"))
        output_root = configured_output if configured_output.is_absolute() else settings.BASE_DIR / configured_output
        retention_guard = output_root.resolve() / ".retention_guard.lock"
        try:
            # Lock all X file and SQLite work as one unit, matching the collector's lock
            # order. This prevents ingest from racing between archive and state cleanup.
            guard = exclusive_file_lock(retention_guard)
            with guard:
                report = purge_retained_data(
                    dsn, getattr(settings, "TG_DB_SCHEMA", "crawler"), settings.NEED_ENGINE_SCHEMA,
                    cutoff, delete=options["delete"],
                )
                report.update(scrub_panel_opportunities(cutoff, delete=options["delete"]))
                report.update(purge_panel_x_posts(cutoff, delete=options["delete"], _retention_locked=True))
                configured_state_path = Path(os.getenv("X_COLLECT_STATE_FILE", "output/x_collector_state.sqlite3"))
                state_path = configured_state_path if configured_state_path.is_absolute() else settings.BASE_DIR / configured_state_path
                report.update(purge_x_collector_state(state_path, cutoff,
                                                      delete=options["delete"], _retention_locked=True))
        except (ValueError, RuntimeError, OSError) as exc:
            raise CommandError(str(exc)) from exc
        mode = "حذف شد" if options["delete"] else "فقط شمارش شد؛ برای حذف --delete بدهید"
        self.stdout.write(f"Retention {days} روز | {mode}")
        for name, count in sorted(report.items()):
            self.stdout.write(f"{name}: {count}")
        if not report:
            self.stdout.write("جدول‌های آرشیو هنوز ساخته نشده‌اند.")
