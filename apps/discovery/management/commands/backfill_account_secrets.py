import logging
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount
from need_engine.crypto import _PREFIX, decrypt_secret, encrypt_secret, is_encrypted

logger = logging.getLogger("remediation.backfill")


class Command(BaseCommand):
    help = "Idempotent batch backfill for social account secrets into versioned enc:v1: format (Finding R08 / P02.09)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Number of records to process per batch (default: 50).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the backfill and report counts without modifying database records.",
        )
        parser.add_argument(
            "--scope",
            choices=["all", "twitter", "instagram"],
            default="all",
            help="Target scope: 'twitter', 'instagram', or 'all' (default: all).",
        )
        parser.add_argument(
            "--include-legacy-scrapers",
            action="store_true",
            help="Explicitly backfill Instagram SCRAPER accounts (Warning: legacy instagram_engine is frozen).",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]
        scope = options["scope"]
        include_legacy = options["include_legacy_scrapers"]

        mode_str = "DRY-RUN (Simulated)" if dry_run else "LIVE BACKFILL"
        self.stdout.write(self.style.NOTICE(f"=== Starting Secret Backfill [{mode_str}] Scope: {scope} ==="))

        stats = {
            "twitter_scanned": 0,
            "twitter_already_encrypted": 0,
            "twitter_backfilled": 0,
            "twitter_quarantine": 0,
            "instagram_scanned": 0,
            "instagram_already_encrypted": 0,
            "instagram_backfilled": 0,
            "instagram_legacy_preserved": 0,
            "instagram_quarantine": 0,
        }

        quarantine_log = []

        if scope in ("all", "twitter"):
            self._backfill_twitter(batch_size, dry_run, stats, quarantine_log)

        if scope in ("all", "instagram"):
            self._backfill_instagram(batch_size, dry_run, include_legacy, stats, quarantine_log)

        self.stdout.write(self.style.SUCCESS("=== Backfill Summary ==="))
        self.stdout.write(f"Twitter Accounts Scanned: {stats['twitter_scanned']}")
        self.stdout.write(f"  - Already Encrypted:   {stats['twitter_already_encrypted']}")
        self.stdout.write(f"  - Plaintext Migrated:  {stats['twitter_backfilled']}")
        self.stdout.write(f"  - Quarantined/Errors:  {stats['twitter_quarantine']}")
        self.stdout.write(f"Instagram Accounts Scanned: {stats['instagram_scanned']}")
        self.stdout.write(f"  - Already Encrypted:     {stats['instagram_already_encrypted']}")
        self.stdout.write(f"  - Plaintext Migrated:    {stats['instagram_backfilled']}")
        self.stdout.write(f"  - Legacy Gate Preserved: {stats['instagram_legacy_preserved']}")
        self.stdout.write(f"  - Quarantined/Errors:    {stats['instagram_quarantine']}")

        if quarantine_log:
            self.stdout.write(self.style.WARNING("Quarantine Notice: The following account IDs had errors and were preserved without wiping:"))
            for item in quarantine_log:
                self.stdout.write(f"  - Target: {item['type']} ID {item['id']} ({item['username']}): {item['reason']}")

    def _backfill_twitter(self, batch_size: int, dry_run: bool, stats: dict, quarantine: list):
        qs = TwitterEngineAccount.objects.order_by("id")
        total = qs.count()
        self.stdout.write(f"Processing TwitterEngineAccount records (Total: {total})...")

        for offset in range(0, total, batch_size):
            batch = list(qs[offset : offset + batch_size])
            for acc in batch:
                stats["twitter_scanned"] += 1
                dirty_fields = []
                acc_quarantined = False

                for field_name in ("password", "auth_token", "ct0"):
                    raw_val = getattr(acc, field_name) or ""
                    if not raw_val:
                        continue
                    if is_encrypted(raw_val):
                        continue

                    # Plaintext detected
                    if dry_run:
                        dirty_fields.append(field_name)
                        continue

                    try:
                        encrypted_val = encrypt_secret(raw_val)
                        # Verification step: ensure round-trip decryption
                        decrypted_val = decrypt_secret(encrypted_val)
                        if decrypted_val != raw_val:
                            raise ValueError(f"Round-trip decryption mismatch on field '{field_name}'")

                        setattr(acc, field_name, encrypted_val)
                        dirty_fields.append(field_name)
                    except Exception as exc:
                        stats["twitter_quarantine"] += 1
                        acc_quarantined = True
                        quarantine.append({
                            "type": "TwitterEngineAccount",
                            "id": acc.id,
                            "username": acc.username,
                            "reason": f"Field '{field_name}' encryption failed: {exc}",
                        })
                        logger.error(
                            "Failed to encrypt TwitterEngineAccount id=%s field=%s: %s (preserving plaintext)",
                            acc.id,
                            field_name,
                            exc,
                        )
                        break

                if acc_quarantined:
                    continue

                if dirty_fields:
                    stats["twitter_backfilled"] += 1
                    if not dry_run:
                        acc.save(update_fields=dirty_fields)
                else:
                    stats["twitter_already_encrypted"] += 1

    def _backfill_instagram(self, batch_size: int, dry_run: bool, include_legacy: bool, stats: dict, quarantine: list):
        qs = InstagramEngineAccount.objects.order_by("id")
        total = qs.count()
        self.stdout.write(f"Processing InstagramEngineAccount records (Total: {total})...")

        for offset in range(0, total, batch_size):
            batch = list(qs[offset : offset + batch_size])
            for acc in batch:
                stats["instagram_scanned"] += 1
                raw_pass = acc.password or ""

                if not raw_pass:
                    continue
                if is_encrypted(raw_pass):
                    stats["instagram_already_encrypted"] += 1
                    continue

                # Plaintext password detected. Check Compatibility Gate:
                if acc.role == "SCRAPER" and not include_legacy:
                    # Legacy frozen instagram_engine reads password directly from ig_accounts.
                    # Preserved for Phase 10 migration per Directive 7 and P02.07.
                    stats["instagram_legacy_preserved"] += 1
                    continue

                if dry_run:
                    stats["instagram_backfilled"] += 1
                    continue

                try:
                    encrypted_pass = encrypt_secret(raw_pass)
                    decrypted_pass = decrypt_secret(encrypted_pass)
                    if decrypted_pass != raw_pass:
                        raise ValueError("Round-trip decryption mismatch on password")

                    acc.password = encrypted_pass
                    acc.save(update_fields=["password"])
                    stats["instagram_backfilled"] += 1
                except Exception as exc:
                    stats["instagram_quarantine"] += 1
                    quarantine.append({
                        "type": "InstagramEngineAccount",
                        "id": acc.id,
                        "username": acc.username,
                        "reason": f"Password encryption failed: {exc}",
                    })
                    logger.error(
                        "Failed to encrypt InstagramEngineAccount id=%s: %s (preserving plaintext)",
                        acc.id,
                        exc,
                    )
