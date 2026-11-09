"""The `ig` service: scrape Instagram for buyer comments on every business's product families (code in ig_scraper/)."""
import time

from django.conf import settings as django_settings
from django.core.management.base import BaseCommand

from apps.discovery.anti_ban import InstagramAntiBanManager
from apps.discovery.ig_scraper import accounts, cycle
from apps.discovery.ig_scraper.context import Run


class Command(BaseCommand):
    help = "Runs Instagram Scraper to collect leads from posts with at least 50 comments."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-comments",
            type=int,
            default=django_settings.INSTAGRAM_MIN_COMMENTS,
            help="Minimum comments required on a post (configurable through INSTAGRAM_MIN_COMMENTS).",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Run continuously in background with periodic discovery cycles.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=5,
            help="Delay in minutes between discovery cycles in loop mode (default: 5).",
        )
        parser.add_argument(
            "--force-hours",
            action="store_true",
            help="Bypass operating hours window check (09:00 - 23:00 Tehran time) for debugging.",
        )

    def handle(self, *args, **options):
        min_comments = options["min_comments"]
        loop_mode = options["loop"]
        interval_minutes = options["interval"]

        self.stdout.write(self.style.MIGRATE_HEADING("=== Instagram Lead Discovery & Dataset Collector ==="))
        self.stdout.write(f"Loop Mode: {loop_mode} (Interval: {interval_minutes}m)")
        self.stdout.write(f"Min Comments Threshold: >= {min_comments} comments")

        import importlib.util

        if importlib.util.find_spec("instagrapi") is None:
            self.stderr.write(self.style.ERROR("instagrapi is not installed in the environment!"))
            return

        run = Run(self.stdout, self.stderr, self.style)
        while True:
            # Operating hours enforcement (09:00 to 23:00 Asia/Tehran)
            if not InstagramAntiBanManager.is_within_operating_hours(options["force_hours"]):
                wait_sec = InstagramAntiBanManager.seconds_until_next_window()
                wait_min = wait_sec / 60.0
                self.stdout.write(self.style.WARNING(
                    f"خارج از ساعات مجاز کاری (۰۹:۰۰ الی ۲۳:۰۰ به وقت تهران). ربات در حالت خواب شبانه قرار دارد ({wait_min:.0f} دقیقه تا آغاز شیفت کاری بعدی)."
                ))
                if loop_mode:
                    sleep_chunk = min(300.0, wait_sec)
                    time.sleep(sleep_chunk)
                    continue
                return

            # 1. Fetch Active Scraper Accounts Pool
            scraper_accounts = accounts.available_scraper_accounts(run)

            if not scraper_accounts:
                self.stderr.write(self.style.WARNING("No active SCRAPER account with stored session found in database!"))
                if loop_mode:
                    time.sleep(60)
                    continue
                return

            account_names = [f"@{a.username} (ID: {a.id})" for a in scraper_accounts]
            self.stdout.write(self.style.SUCCESS(f"Active scraper accounts pool ({len(scraper_accounts)} accounts): {', '.join(account_names)}"))

            targets = cycle.build_targets(run)
            self.stdout.write(f"\nProcessing {len(targets)} business discovery targets...")
            cycle.purge_old_post_hashes(run)
            cycle_leads = cycle.scrape_targets(run, scraper_accounts, targets, min_comments)

            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== Discovery Cycle Complete ({cycle_leads} leads captured) ==="))

            if not loop_mode:
                break

            self.stdout.write(self.style.NOTICE(f"Sleeping for {interval_minutes} minutes before next cycle..."))
            time.sleep(interval_minutes * 60)
