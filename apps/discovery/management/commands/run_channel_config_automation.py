import logging
import time
from django.core.management.base import BaseCommand

from apps.discovery.channel_config_automation import (
    sync_channel_configs,
    sync_all_active_channels,
)

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Automated Telegram Channel Proxy Config Collector with Dynamic Multi-Channel Support"

    def add_arguments(self, parser):
        parser.add_argument(
            "--channel",
            default="",
            help="Target channel username (e.g. AR14N24B). If empty, syncs all active channels.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Sync all active channels registered in the monitoring pool.",
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
        raw_channel = options.get("channel", "").strip().lstrip("@")
        sync_all = options.get("all") or not raw_channel or raw_channel.lower() == "all"
        loop = options.get("loop")
        interval = max(options.get("interval") or 180, 30)
        max_posts = options.get("max_posts") or 15
        max_add = options.get("max_add") or 10
        force_all = options.get("force_all")

        target_desc = "تمام کانال‌های فعال در استخر پایش" if sync_all else f"@{raw_channel}"
        self.stdout.write(
            self.style.SUCCESS(
                f"🚀 [اتوماسیون استخراج و تست زنده کانفیگ‌های تلگرام]\n"
                f"   • هدف: {target_desc}\n"
                f"   • حالت اجرا: {'حلقه مداوم (Background Loop)' if loop else 'تک‌مرحله‌ای (Single Run)'}\n"
                f"   • فاصله بررسی: {interval} ثانیه | سقف افزودن در هر دور: {max_add}"
            )
        )

        cycle = 1
        while True:
            self.stdout.write(
                self.style.NOTICE(f"\n🔄 [دور {cycle}] در حال بررسی پیام‌های تازه در {target_desc}...")
            )
            try:
                if sync_all:
                    res = sync_all_active_channels(max_posts=max_posts, max_add_per_channel=max_add)
                    if res.get("success"):
                        self.stdout.write(self.style.SUCCESS(f"✅ [دور {cycle}] {res.get('message')}"))
                    else:
                        self.stderr.write(self.style.ERROR(f"❌ [دور {cycle}] خطا: {res.get('message')}"))
                else:
                    res = sync_channel_configs(
                        channel_username=raw_channel,
                        max_posts=max_posts,
                        force_all=force_all and (cycle == 1),
                        max_add_per_run=max_add,
                    )
                    if res.get("success"):
                        added = res.get("added_count", 0)
                        checked = res.get("posts_checked", 0)
                        tested = res.get("configs_tested", 0)
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"✅ [دور {cycle}] {res.get('message')} "
                                f"(پست‌های بررسی‌شده: {checked} | کانفیگ‌های تست‌شده: {tested} | افزوده‌شده: {added})"
                            )
                        )
                    else:
                        self.stderr.write(
                            self.style.ERROR(f"❌ [دور {cycle}] خطا در همگام‌سازی @{raw_channel}: {res.get('error')}")
                        )
            except Exception as exc:
                log.warning("Channel config sync cycle %s failed", cycle, exc_info=True)
                self.stderr.write(self.style.ERROR(f"❌ خطای غیرمنتظره در دور {cycle}: {exc}"))

            if not loop:
                break

            self.stdout.write(f"⏳ خواب {interval} ثانیه‌ای تا دور بعدی...")
            time.sleep(interval)
            cycle += 1
