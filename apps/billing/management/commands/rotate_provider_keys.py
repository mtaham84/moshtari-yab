import os

from django.core.management.base import BaseCommand, CommandError

from apps.billing.models import Provider
from need_engine.crypto import reencrypt_secret


class Command(BaseCommand):
    help = "Re-encrypt all provider API keys with FIELD_ENCRYPTION_KEY."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count keys without changing them.")

    def handle(self, *args, **options):
        if not os.environ.get("FIELD_ENCRYPTION_KEY", "").strip():
            raise CommandError("Set FIELD_ENCRYPTION_KEY to the new Fernet key first.")
        changed = 0
        for provider in Provider.objects.exclude(api_key="").only("pk", "api_key").iterator(chunk_size=500):
            try:
                encrypted = reencrypt_secret(provider.api_key)
            except RuntimeError as exc:
                raise CommandError(f"Cannot rotate provider key for provider id {provider.pk}: {exc}") from exc
            if not options["dry_run"]:
                # QuerySet.update bypasses field preparation, avoiding double encryption.
                Provider.objects.filter(pk=provider.pk).update(api_key=encrypted)
            changed += 1
        verb = "Would re-encrypt" if options["dry_run"] else "Re-encrypted"
        self.stdout.write(self.style.SUCCESS(f"{verb} {changed} provider API key(s)."))
