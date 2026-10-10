"""Create/update the GLOBAL sources (groups watched for every seller) from a text file.

    python manage.py seed_global_sources docker/global_sources.txt [--deactivate-missing]

One link per line (@name, t.me/name, https://t.me/+invite); text after the link is used as the name; # comments.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.discovery.models import MonitoredCommunity
from apps.discovery.sources import GLOBAL, normalize_link


class Command(BaseCommand):
    help = "Create or update global Telegram sources (shared by every seller) from a file."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--deactivate-missing", action="store_true",
                            help="Deactivate global sources that are not in the file.")

    def handle(self, path, deactivate_missing=False, **opts):
        try:
            with open(path, encoding="utf-8") as fh:
                lines = [ln.split("#", 1)[0].strip() for ln in fh]
        except OSError as exc:
            raise CommandError(f"cannot read {path}: {exc}")
        seen, created, updated = set(), 0, 0
        for line in filter(None, lines):
            link, _, name = line.partition(" ")
            key = normalize_link(link)
            if not key or key in seen:
                continue
            seen.add(key)
            obj = MonitoredCommunity.objects.filter(scope=GLOBAL, normalized_link=key).first()
            if obj is None:
                obj = MonitoredCommunity(business=None, handle_or_link=link)
                created += 1
            else:
                updated += 1
            obj.is_active = True
            if name.strip():
                obj.name = name.strip()
            obj.save()
        off = 0
        if deactivate_missing:
            off = MonitoredCommunity.objects.filter(scope=GLOBAL, is_active=True).exclude(normalized_link__in=seen).update(is_active=False)
        self.stdout.write(self.style.SUCCESS(f"global sources: {created} created, {updated} updated, {off} deactivated"))
