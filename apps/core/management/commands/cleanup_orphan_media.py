from django.core.management.base import BaseCommand
from apps.core.image_service import sweep_orphan_media


class Command(BaseCommand):
    help = "Scan MEDIA_ROOT for orphan (unreferenced) files and safely quarantine or report them."

    def add_arguments(self, parser):
        parser.add_argument(
            "--grace-hours",
            type=int,
            default=24,
            help="Grace period in hours (defaults to 24). Files modified more recently are not touched.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Perform actual sweep. Without this flag, only reports findings in dry-run mode.",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Permanently delete orphans instead of moving them to media/quarantine/.",
        )

    def handle(self, *args, **options):
        dry_run = not options["apply"]
        grace_hours = max(0, options["grace_hours"])
        quarantine = not options["delete"]

        mode_str = "DRY-RUN (گزارش بدون تغییر)" if dry_run else ("قرنطینه" if quarantine else "حذف دائمی")
        self.stdout.write(self.style.NOTICE(f"=== آغاز بررسی فایل‌های رهاشده در مدیا [حالت: {mode_str}] ==="))
        self.stdout.write(f"مهلت تغییرات اخیر (Grace Period): {grace_hours} ساعت")

        stats = sweep_orphan_media(
            grace_hours=grace_hours,
            dry_run=dry_run,
            quarantine=quarantine,
        )

        self.stdout.write(f"تعداد کل فایل‌های اسکن‌شده: {stats['scanned']}")
        self.stdout.write(f"فایل‌های دارای مرجع فعال در دیتابیس: {stats['referenced']}")
        self.stdout.write(f"فایل‌های تازه‌تغییریافته درون مهلت (Grace): {stats['in_grace']}")
        self.stdout.write(self.style.WARNING(f"تعداد فایل‌های رهاشده (Orphans): {stats['orphans']}"))

        if not dry_run:
            if quarantine:
                self.stdout.write(self.style.SUCCESS(f"تعداد فایل‌های منتقل‌شده به قرنطینه: {stats['quarantined']}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"تعداد فایل‌های حذف‌شده: {stats['deleted']}"))
        else:
            self.stdout.write(self.style.NOTICE("توجه: این اجرا به‌صورت آزمایشی (dry-run) بود. هیچ فایلی تغییر نکرد. برای اعمال تغییرات از --apply استفاده کنید."))

        if stats["orphans"] > 0 and dry_run:
            self.stdout.write("\nنمونه‌ای از فایل‌های رهاشده شناسایی‌شده:")
            for orphan in stats["orphan_list"][:10]:
                self.stdout.write(f" - {orphan}")
            if len(stats["orphan_list"]) > 10:
                self.stdout.write(f" ... و {len(stats['orphan_list']) - 10} فایل دیگر.")
