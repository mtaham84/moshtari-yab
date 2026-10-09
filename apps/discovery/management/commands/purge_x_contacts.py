from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.discovery.models import Customer


class Command(BaseCommand):
    help = "Purge public contact details from old X customer records."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-days", type=int, default=90)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=max(1, options["older_than_days"]))
        count = Customer.objects.filter(source_platform="x", opportunities__created_at__lt=cutoff).distinct().update(
            contact_channels=[], linked_identities=[])
        self.stdout.write(f"Purged contacts from {count} X customer records.")
