from django.core.management.base import BaseCommand, CommandError

from apps.discovery import accounts_pool_file as pool


class Command(BaseCommand):
    help = "Export/import the git-ignored accounts pool file (Instagram + X accounts, sessions, cookies)."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["export", "import", "path"])
        parser.add_argument("--force", action="store_true", help="import: overwrite non-empty DB values too")

    def handle(self, *args, **o):
        path = pool.pool_path()
        if o["action"] == "path":
            self.stdout.write(str(path))
        elif o["action"] == "export":
            pool.export_pool(force=True)
            self.stdout.write(self.style.SUCCESS(f"exported -> {path}"))
        else:
            if not path.exists():
                raise CommandError(f"{path} does not exist")
            self.stdout.write(self.style.SUCCESS(f"imported from {path}: {pool.import_pool(force=o['force'])}"))
