"""
Django Management Command: check_outreach_health

Inspects the live health of Instagram outreach senders, lead queues,
proxy connectivity, and shift schedules to identify why DMs are paused.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
from django.core.management.base import BaseCommand
from django.utils import timezone as django_tz

from apps.discovery.ig_accounts import tenant_ids as ig_tenant_ids

from apps.discovery.models.instagram import InstagramEngineAccount, InstagramEngineLead
from apps.discovery.human_routine import HumanShiftPlanner

TEHRAN_TZ = ZoneInfo("Asia/Tehran")


class Command(BaseCommand):
    help = "Diagnoses Instagram outreach engine health, sender statuses, and lead queues."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            default="tenant_4",
            help="Tenant ID to inspect (default: tenant_4).",
        )

    def handle(self, *args, **options):
        tenant = options["tenant"]
        tenant_ids = ig_tenant_ids(tenant)   # includes legacy aliases such as "sarv"

        now_utc = django_tz.now()
        now_tehran = datetime.now(TEHRAN_TZ)

        self.stdout.write(self.style.SUCCESS("=" * 70))
        self.stdout.write(self.style.SUCCESS("🩺 [گزارش عیب‌یابی جامع موتور ارسال دایرکت (Outreach Health Check)]"))
        self.stdout.write(f"⏰ زمان فعلی سرور: {now_tehran.strftime('%Y-%m-%d %H:%M:%S')} (تهران)")
        self.stdout.write(f"🏢 مستأجر هدف: {tenant_ids}")
        self.stdout.write(self.style.SUCCESS("=" * 70))

        # 1. Shift Schedule Diagnostic
        is_active, shift_reason, wait_sec = HumanShiftPlanner.is_shift_active(now_tehran)
        today_sched = HumanShiftPlanner.get_or_create_daily_schedule(now_tehran.date())
        self.stdout.write(self.style.MIGRATE_HEADING("\n1️⃣ وضعیت شیفت کاری انسان‌گونه (HumanShiftPlanner):"))
        self.stdout.write(f"   • شیفت صبح: {today_sched.morning_start.strftime('%H:%M')} الی {today_sched.morning_end.strftime('%H:%M')}")
        self.stdout.write(f"   • شیفت عصر: {today_sched.afternoon_start.strftime('%H:%M')} الی {today_sched.afternoon_end.strftime('%H:%M')}")
        self.stdout.write(f"   • وضعیت لحظه‌ای: {'🟢 در حال کار' if is_active else '🔴 متوقف / خواب'}")
        self.stdout.write(f"   • علت: {shift_reason}")
        if not is_active:
            self.stdout.write(self.style.WARNING(f"   ⚠️ در صورت اجرای بدون فلگ --ignore-circadian، سیستم تا {wait_sec // 60} دقیقه دیگر پیامی ارسال نخواهد کرد."))

        # 2. Senders Diagnostic
        self.stdout.write(self.style.MIGRATE_HEADING("\n2️⃣ وضعیت اکانت‌های ارسال‌کننده (SENDER Accounts):"))
        senders = list(InstagramEngineAccount.objects.filter(role="SENDER", tenant_id__in=tenant_ids))
        if not senders:
            self.stdout.write(self.style.ERROR("   ❌ هیچ اکانتی با نقش SENDER برای این مستأجر در دیتابیس ثبت نشده است!"))
        else:
            for s in senders:
                is_available = s.is_available()
                status_color = self.style.SUCCESS if (s.status == "ACTIVE" and is_available) else self.style.ERROR
                cd_str = ""
                if s.cooldown_until and s.cooldown_until > now_utc:
                    cd_mins = int((s.cooldown_until - now_utc).total_seconds() / 60)
                    cd_str = f" | استراحت تا {cd_mins} دقیقه دیگر"

                last_act = s.last_action_at.astimezone(TEHRAN_TZ).strftime("%H:%M:%S") if s.last_action_at else "هنوز فعالیتی ثبت نشده"
                self.stdout.write(status_color(
                    f"   • @{s.username} | وضعیت: {s.status} ({s.get_status_display()}){cd_str}\n"
                    f"     - رده اکانت: {s.account_tier} | ارسال امروز: {s.daily_action_count}/{s.daily_limit or 18} | این ساعت: {s.hourly_action_count or 0}/{s.hourly_limit or 3}\n"
                    f"     - آخرین فعالیت: {last_act}\n"
                    f"     - پروکسی: {s.proxy_url or 'بدون پروکسی'}\n"
                    f"     - پیام وضعیت: {s.status_message or 'ندارد'}"
                ))

        # 3. Lead Queue Diagnostic
        self.stdout.write(self.style.MIGRATE_HEADING("\n3️⃣ وضعیت صف لیدها (Leads Queue):"))
        total_leads = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids).count()
        new_leads = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids, status="NEW").count()
        high_intent_new = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids, status="NEW", intent_score__gte=0.70).count()
        queued_dms = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids, status="QUEUED").count()
        dm_sent = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids, status="DM_SENT").count()
        replied = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids, status="REPLIED").count()
        skipped = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids, status="SKIPPED").count()
        failed = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids, status="FAILED").count()

        self.stdout.write(f"   • کل لیدهای ثبت‌شده: {total_leads}")
        self.stdout.write(f"   • لیدهای جدید در انتظار (NEW): {new_leads} (تعداد دارای امتیاز بالای ۷۰٪: {high_intent_new})")
        self.stdout.write(f"   • پیام‌های در صف ارسال اپراتور (QUEUED): {queued_dms}")
        self.stdout.write(f"   • دایرکت‌های ارسال شده (DM_SENT): {dm_sent}")
        self.stdout.write(f"   • مشتریانی که پاسخ داده‌اند (REPLIED): {replied}")
        self.stdout.write(f"   • رد شده توسط فیلتر ایمنی (SKIPPED): {skipped}")
        self.stdout.write(f"   • خطای ارسال (FAILED): {failed}")

        if high_intent_new == 0 and queued_dms == 0:
            self.stdout.write(self.style.WARNING("   ⚠️ صف ارسال خالی است! هیچ لید جدیدی با احتمال خرید بالای ۷۰٪ در دیتابیس وجود ندارد."))

        # 4. Recent Skipped Reasons
        if skipped > 0:
            self.stdout.write(self.style.MIGRATE_HEADING("\n4️⃣ نمونه آخرین دلایل رد شدن لیدها (SKIPPED):"))
            recent_skipped = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids, status="SKIPPED").order_by("-updated_at")[:3]
            for r in recent_skipped:
                self.stdout.write(f"   • @{r.username}: {r.comment_text[-120:].strip()}")

        self.stdout.write(self.style.SUCCESS("\n" + "=" * 70))
