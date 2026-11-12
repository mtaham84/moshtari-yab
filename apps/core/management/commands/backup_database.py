"""Management command: backup_database.

Runs the encrypted PostgreSQL backup pipeline (see ``apps/core/backup.py``):
  1. Validate config & free disk space (skips with Telegram alert if low)
  2. ``pg_dump -Fc`` -> GPG symmetric encryption -> split -> Telegram upload
  3. Rotate old local backup sets

Exit code:
  0  ok or skipped
  1  error (also sends a Telegram alert)
"""
from __future__ import annotations

import sys

from django.core.management.base import BaseCommand

from apps.core.backup import free_mb_on, load_backup_config, run_backup


class Command(BaseCommand):
    help = "Create an encrypted PostgreSQL backup and send it to Telegram."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only check free disk space; do not dump or upload.",
        )

    def handle(self, *args, **options) -> None:
        if options["dry_run"]:
            cfg = load_backup_config()
            free = free_mb_on(cfg.backup_dir)
            self.stdout.write(
                f"Free disk on {cfg.backup_dir}: {free} MB "
                f"(minimum required: {cfg.min_free_mb} MB)"
            )
            if free < cfg.min_free_mb:
                self.stderr.write(self.style.ERROR("Would skip — not enough free disk."))
            else:
                self.stdout.write(self.style.SUCCESS("Would proceed with backup."))
            return

        result = run_backup()

        if result["status"] == "ok":
            self.stdout.write(
                self.style.SUCCESS(
                    f"Backup completed: {result['parts']} part(s) [{result['stamp']}]"
                )
            )
        elif result["status"] == "skipped":
            self.stdout.write(
                self.style.WARNING(
                    f"Backup skipped: {result['free_mb']} MB free "
                    f"(minimum {result['min_free_mb']} MB)"
                )
            )
        else:
            self.stderr.write(
                self.style.ERROR(
                    f"Backup failed [{result.get('stamp', '?')}]: {result.get('error')}"
                )
            )
            sys.exit(1)
