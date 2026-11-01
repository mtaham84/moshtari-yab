"""Django management command to regenerate and replace search queries for X and Instagram
strictly at the active Leaf Category level.

Usage:
  python manage.py regenerate_platform_queries --all-tenants
  python manage.py regenerate_platform_queries --business-id=1 --dry-run
  python manage.py regenerate_platform_queries --all-tenants --platform=x
"""
from __future__ import annotations

import logging
from django.core.management.base import BaseCommand
from apps.businesses.models import Business
from apps.discovery.leaf_query_generator import replace_platform_queries_for_business

logger = logging.getLogger("discovery.commands.regenerate_platform_queries")


class Command(BaseCommand):
    help = "بازسازی و جایگزینی کوئری‌های فعال پلتفرم‌های X و اینستاگرام بر اساس دسته‌های برگ (نسخه ۲)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--business-id",
            type=int,
            help="شناسه کسب‌وکار خاص (برای یک مستأجر معین)",
        )
        parser.add_argument(
            "--all-tenants",
            action="store_true",
            help="اجرای فرآیند برای تمامی کسب‌وکارهای فعال سیستم",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="اجرای آزمایشی و پیش‌نمایش کوئری‌ها بدون تغییر در دیتابیس",
        )
        parser.add_argument(
            "--platform",
            type=str,
            default="all",
            choices=["all", "x", "instagram", "ig"],
            help="پلتفرم هدف: all (پیش‌فرض), x, یا instagram",
        )

    def handle(self, *args, **options):
        business_id = options.get("business_id")
        all_tenants = options.get("all_tenants")
        dry_run = options.get("dry_run", False)
        platform = options.get("platform", "all")

        if not business_id and not all_tenants:
            self.stderr.write(self.style.ERROR("لطفاً یکی از گزینه‌های --business-id یا --all-tenants را مشخص کنید."))
            return

        if business_id:
            businesses = Business.objects.filter(id=business_id)
            if not businesses.exists():
                self.stderr.write(self.style.ERROR(f"کسب‌وکار با شناسه {business_id} یافت نشد."))
                return
        else:
            businesses = Business.objects.order_by("id")

        mode_str = " (حالت پیش‌نمایش آزمایشی DRY-RUN)" if dry_run else ""
        self.stdout.write(self.style.HTTP_INFO(
            f"=== آغاز بازسازی کوئری‌های پلتفرم‌ها برای {businesses.count()} کسب‌وکار{mode_str} ==="
        ))

        total_processed = 0
        total_leaf_cats = 0
        errors_count = 0

        for biz in businesses:
            self.stdout.write(f"\n--- بررسی مستأجر: {biz.name} (شناسه: {biz.id}) ---")
            try:
                res = replace_platform_queries_for_business(biz, dry_run=dry_run, platform=platform)
                total_processed += 1
                leaf_count = res.get("leaf_categories_count", 0)
                total_leaf_cats += leaf_count

                if res.get("errors"):
                    errors_count += len(res["errors"])
                    for err in res["errors"]:
                        self.stderr.write(self.style.ERROR(f"  خطا: {err}"))
                    continue

                if leaf_count == 0:
                    self.stdout.write(self.style.WARNING("  هیچ دسته برگ فعالی با محصول فعال یافت نشد."))
                    continue

                self.stdout.write(self.style.SUCCESS(
                    f"  تعداد {leaf_count} دسته برگ با موفقیت پردازش شد."
                ))
                for cat in res.get("leaf_categories", []):
                    self.stdout.write(f"    دسته برگ: {cat['category_path']} (عمق: {cat['category_depth']})")
                    if cat.get("x_queries"):
                        self.stdout.write(f"      کوئری‌های X ({len(cat['x_queries'])}): {cat['x_queries']}")
                    if cat.get("ig_queries"):
                        self.stdout.write(f"      کوئری‌های اینستاگرام ({len(cat['ig_queries'])}): {cat['ig_queries']}")

            except Exception as exc:
                errors_count += 1
                self.stderr.write(self.style.ERROR(f"  خطای پردازش مستأجر {biz.id}: {exc}"))

        self.stdout.write(self.style.HTTP_INFO(
            f"\n=== پایان عملیات: {total_processed} مستأجر پردازش شد، {total_leaf_cats} دسته برگ، {errors_count} خطا ==="
        ))
