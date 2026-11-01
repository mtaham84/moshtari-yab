from django.core.management.base import BaseCommand
from django.conf import settings

from need_engine.config import EngineConfig
from need_engine.store import Store


class Command(BaseCommand):
    help = "Explicitly bootstraps or migrates need_engine PostgreSQL schema and tables (P07.02 / R33)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            type=str,
            default=None,
            help="Optional target schema override (defaults to NEED_ENGINE_SCHEMA).",
        )

    def handle(self, *args, **options):
        schema = options["schema"] or getattr(settings, "NEED_ENGINE_SCHEMA", "need_engine")
        cfg = EngineConfig()
        dsn = cfg.database_url

        self.stdout.write(self.style.NOTICE(f"Migrating engine schema '{schema}' on {dsn.split('@')[-1]}..."))

        # Initialize with create=False, then run explicit bootstrap_schema
        store = Store(dsn, schema=schema, create=False)
        store.bootstrap_schema()

        # Validate with read-only preflight
        store._preflight_schema()
        store.close()

        self.stdout.write(self.style.SUCCESS(f"Engine schema '{schema}' successfully bootstrapped and validated."))
