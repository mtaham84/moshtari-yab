import sys
import time
from django.core.management.base import BaseCommand

from apps.discovery.channel_config_automation import (
    AUTHORIZED_CHANNELS,
    sync_channel_configs,
    load_channel_state,
)


class Command(BaseCommand):
    help = "Automated Telegram Channel Proxy Config Collector (Strictly for @AR14N24B)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--channel",
            default="AR14N24B",
            help="Target channel username (Default: AR14N24B - strictly restricted).",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Run continuously in an automated background loop.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=180,
            help="Interval in seconds between crawling cycles in loop mode (default: 180).",
        )
        parser.add_argument(
            "--max-posts",
            type=int,
            default=15,
            help="Maximum number of new posts to inspect per cycle (default: 15).",
        )
        parser.add_argument(
            "--max-add",
            type=int,
            default=10,
            help="Maximum number of healthy configs to add per cycle (default: 10).",
        )
        parser.add_argument(
            "--force-all",
            action="store_true",
            help="Ignore last processed post ID and inspect all recent posts.",
        )

    def handle(self, *args, **options):
        channel = options["channel"].strip().lstrip("@")
        loop = options["loop"]
        interval = max(options["interval"], 30)
        max_posts = options["max_posts"]
        max_add = options["max_add"]
        force_all = options["force_all"]

        if channel.upper() not in [c.upper() for c in AUTHORIZED_CHANNELS]:
            self.stderr.write(
                self.style.ERROR(
                    f"❌ خطای دسترسی امنیتی: کانال '{channel}' مجاز نمی‌باشد. "
                    f"طبق دستور، اتوماسیون تنها مجاز به دریافت کانفیگ از کانال {AUTHORIZED_CHANNELS[0]} است."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"🚀 [اتوماسیون استخراج و تست زنده کانفیگ‌های تلگرام]\n"
                f"   • کانال مجاز: https://t.me/{channel}\n"
                f"   • حالت اجرا: {'حلقه مداوم (Background Loop)' if loop else 'تک‌مرحله‌ای (Single Run)'}\n"
                f"   • فاصله بررسی: {interval} ثانیه | سقف افزودن در هر دور: {max_add}"
            )
        )

        cycle = 1
        while True:
            self.stdout.write(
                self.style.NOTICE(f"\n🔄 [دور {cycle}] در حال بررسی پیام‌های تازه در کانال @{channel}...")
            )
            try:
                res = sync_channel_configs(
                    channel_username=channel,
                    max_posts=max_posts,
                    force_all=force_all and (cycle == 1),
                    max_add_per_run=max_add,
                )
                if res.get("success"):
                    added = res.get("added_count", 0)
                    checked = res.get("posts_checked", 0)
                    found = res.get("configs_found", 0)
                    tested = res.get("configs_tested", 0)
                    if added > 0:
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"   ✅ {added} کانفیگ سالم تست و به استخر پروکسی اضافه شد! "
                                f"(بررسی {checked} پست، {found} کانفیگ یافت‌شده، {tested} تست‌شده)"
                            )
                        )
                        for item in res.get("added_configs", []):
                            self.stdout.write(
                                f"      • پورت {item.get('local_port')}: {item.get('tag')} "
                                f"({item.get('protocol')} - تاخیر {item.get('ping_ms')}ms)"
                            )
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f"   ℹ️ {res.get('message', 'کانفیگ جدید یا سالمی برای اضافه شدن یافت نشد.')}"
                            )
                        )
                else:
                    self.stderr.write(
                        self.style.ERROR(f"   ❌ خطا: {res.get('error')}")
                    )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"   ❌ خطای غیرمنتظره در اجرای اتوماسیون: {exc}"))

            if not loop:
                break

            cycle += 1
            self.stdout.write(f"⏳ انتظار {interval} ثانیه‌ای تا چرخه بعدی...")
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("\nتوقف اتوماسیون توسط کاربر."))
                break
