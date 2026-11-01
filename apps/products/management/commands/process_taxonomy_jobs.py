from django.core.management.base import BaseCommand

from apps.products.taxonomy_worker import run_taxonomy_worker_once


class Command(BaseCommand):
    help = "Processes pending taxonomy organisation jobs using durable lease-based workers (P07.04 / R24)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--max-jobs",
            type=int,
            default=10,
            help="Maximum number of jobs to process before exiting.",
        )

    def handle(self, *args, **options):
        max_jobs = options["max_jobs"]
        self.stdout.write(self.style.NOTICE(f"Starting taxonomy worker (max_jobs={max_jobs})..."))

        processed = 0
        while processed < max_jobs:
            ran = run_taxonomy_worker_once()
            if not ran:
                break
            processed += 1

        if processed:
            self.stdout.write(self.style.SUCCESS(f"Successfully processed {processed} taxonomy jobs."))
        else:
            self.stdout.write("No pending taxonomy jobs in queue.")
