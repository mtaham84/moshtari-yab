"""
Management command to run automated, intelligent, and anti-ban Instagram Direct Outreach.

Features:
1. Strict Role Isolation: Only SENDER accounts are used; SCRAPERS are NEVER touched.
2. Distinct Identity & Hardware Profiles:
   - @raha.rostamim  (Xiaomi 14 Pro, Consultant: رها)
   - @Pania.mehrim   (Google Pixel 8 Pro, Consultant: پنیا)
3. Personalized Account-Specific Pitches (from data/sarv_outreach_config.json).
4. Strict Duplicate Prevention (No Cross-Messaging):
   - Atomically updates lead status from NEW to DM_SENT immediately upon send.
5. High-Intent Lead Prioritization:
   - Always contacts highest intent_score leads first.
6. Anti-Ban Human Emulation:
   - Circadian rhythm enforcement (09:00 - 23:00 Tehran time).
   - Profile visit warm-up before sending.
   - Natural human typing delay (15-35 seconds).
   - Safety pacing delay between consecutive DMs (default 4 to 8 minutes).
   - Daily DM caps per account.
"""

import json
import logging
import random
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

from django.core.management.base import BaseCommand
from django.utils import timezone as django_tz
from django.db import transaction

try:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))

from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
from apps.discovery.device_emulator import apply_device_profile_to_client

logger = logging.getLogger("instagram_outreach")

CONFIG_FILE = Path("data/sarv_outreach_config.json")

DEFAULT_SENDER_PITCHES = {
    "raha.rostamim": {
        "consultant_name": "رها",
        "consultant_role": "مشاور و پشتیبانی تخصصی دندان‌پزشکی",
        "template": "درود بر شما \nبنده رها هستم مشاور و پشتیبانی تخصصی دندان پزشکی ، متوجه شدم پیگیر امور زیبایی و سلامتی دندونتون هستید\nمیتونید هر سوالی دارید از بنده به عنوان متخصص بپرسید. \nاگر هم فکر میکنید موثر هست ، شماره تماستون رو بزارید من برای 10 دقیقه مشاوره تخصصی و بدون چشم داشت با شما تماس میگیرم",
    },
    "pania.mehrim": {
        "consultant_name": "پنیا",
        "consultant_role": "مشاور و پشتیبانی تخصصی دندان‌پزشکی",
        "template": "درود بر شما \nبنده پنیا هستم مشاور و پشتیبانی تخصصی دندان پزشکی ، متوجه شدم پیگیر امور زیبایی و سلامتی دندونتون هستید\nمیتونید هر سوالی دارید از بنده به عنوان متخصص بپرسید \nاگر هم فکر میکنید موثر هست ، شماره تماستون رو بزارید من برای 10 دقیقه مشاوره تخصصی و بدون چشم داشت با شما تماس میگیرم",
    },
}


def load_outreach_config() -> dict:
    pitches = dict(DEFAULT_SENDER_PITCHES)
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for u, p in cfg.get("sender_pitches", {}).items():
                pitches[u.lower()] = p
            return cfg
        except Exception as exc:
            logger.warning(f"Could not load {CONFIG_FILE}: {exc}")
    return {"sender_pitches": pitches}


class Command(BaseCommand):
    help = "Run automated Instagram Direct Outreach for high-intent leads using SENDER accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            default="tenant_4",
            help="Tenant ID or keyword (e.g. 'tenant_4' or 'sarv'). Default: tenant_4",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate the entire process without actually sending DMs via Instagram API.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process a single batch and exit without looping.",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Run continuously in a loop.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=5,
            help="Number of leads to process per batch (default: 5).",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=15,
            help="Minutes to sleep between batches in loop mode (default: 15).",
        )
        parser.add_argument(
            "--min-intent",
            type=float,
            default=0.70,
            help="Minimum intent score threshold (default: 0.70).",
        )
        parser.add_argument(
            "--delay-min",
            type=int,
            default=240,
            help="Minimum delay in seconds between consecutive DMs (default: 240s / 4m).",
        )
        parser.add_argument(
            "--delay-max",
            type=int,
            default=480,
            help="Maximum delay in seconds between consecutive DMs (default: 480s / 8m).",
        )
        parser.add_argument(
            "--max-daily-dms",
            type=int,
            default=18,
            help="Maximum daily DMs per sender account (default: 18).",
        )
        parser.add_argument(
            "--ignore-circadian",
            action="store_true",
            help="Bypass circadian hours check (for testing outside 09:00 - 23:00).",
        )

    def handle(self, *args, **options):
        tenant = options["tenant"]
        dry_run = options["dry_run"]
        loop = options["loop"]
        once = options["once"]
        batch_size = options["batch_size"]
        interval = options["interval"]
        min_intent = options["min_intent"]
        delay_min = options["delay_min"]
        delay_max = options["delay_max"]
        max_daily_dms = options["max_daily_dms"]
        ignore_circadian = options["ignore_circadian"]

        # Support alias 'sarv' and 'tenant_4'
        tenant_ids = [tenant]
        if tenant in ("sarv", "tenant_4"):
            tenant_ids = ["tenant_4", "sarv"]

        self.stdout.write(self.style.SUCCESS("=" * 75))
        self.stdout.write(self.style.SUCCESS("🚀 [موتور اختصاصی ارسال دایرکت هوشمند اینستاگرام (Instagram Outreach)]"))
        self.stdout.write(self.style.SUCCESS(f"📌 هدف: مستأجر {tenant_ids} | حداقل احتمال خرید: {min_intent} | حالت تست: {dry_run}"))
        self.stdout.write(self.style.SUCCESS("=" * 75))

        cfg = load_outreach_config()
        sender_pitches = cfg.get("sender_pitches", {})

        clients_cache: Dict[str, Any] = {}

        def get_or_create_client(account: InstagramEngineAccount):
            if account.username in clients_cache:
                return clients_cache[account.username]

            from instagrapi import Client

            cl = Client()
            if account.proxy_url:
                cl.set_proxy(account.proxy_url)

            try:
                apply_device_profile_to_client(cl, account)
            except Exception as dev_err:
                self.stderr.write(f"Warning setting device profile for @{account.username}: {dev_err}")

            if account.session_json:
                cookies = account.session_json.get("cookies", {})
                sess_id = cookies.get("sessionid")
                if sess_id:
                    try:
                        cl.login_by_sessionid(sess_id)
                    except Exception as login_err:
                        self.stderr.write(f"Error logging in by sessionid for @{account.username}: {login_err}")
                        raise

            clients_cache[account.username] = cl
            return cl

        def is_within_circadian_hours() -> bool:
            if ignore_circadian:
                return True
            now_tehran = datetime.now(TEHRAN_TZ)
            return 9 <= now_tehran.hour < 23

        def get_available_senders() -> List[InstagramEngineAccount]:
            now = django_tz.now()
            # Reset daily counts if last action was on a previous day
            for acc in InstagramEngineAccount.objects.filter(role="SENDER"):
                if acc.last_action_at and acc.last_action_at.astimezone(TEHRAN_TZ).date() < datetime.now(TEHRAN_TZ).date():
                    acc.daily_action_count = 0
                    acc.save(update_fields=["daily_action_count"])

            senders = InstagramEngineAccount.objects.filter(
                role="SENDER",
                status="ACTIVE",
                tenant_id__in=tenant_ids,
            ).filter(
                cooldown_until__isnull=True
            ) | InstagramEngineAccount.objects.filter(
                role="SENDER",
                status="ACTIVE",
                tenant_id__in=tenant_ids,
                cooldown_until__lte=now,
            )

            available = [s for s in senders if s.daily_action_count < max_daily_dms]
            # Order by least recently used
            available.sort(key=lambda s: s.last_action_at or datetime.min.replace(tzinfo=timezone.utc))
            return available

        def execute_batch() -> int:
            if not is_within_circadian_hours():
                now_tehran_str = datetime.now(TEHRAN_TZ).strftime("%H:%M")
                self.stdout.write(self.style.WARNING(
                    f"💤 [CIRCADIAN PAUSE] ساعت محلی تهران {now_tehran_str} است. ارسال فقط در ساعات ۹:۰۰ تا ۲۳:۰۰ مجاز است."
                ))
                return 0

            available_senders = get_available_senders()
            if not available_senders:
                self.stdout.write(self.style.WARNING("⚠️ هیچ اکانت فرستنده (SENDER) با ظرفیت خالی در حال حاضر در دسترس نیست."))
                return 0

            self.stdout.write(f"👥 اکانت‌های فرستنده در دسترس: {[f'@{s.username} ({s.daily_action_count}/{max_daily_dms})' for s in available_senders]}")

            # Fetch top priority NEW leads
            leads = list(
                InstagramEngineLead.objects.filter(
                    tenant_id__in=tenant_ids,
                    status="NEW",
                    intent_score__gte=min_intent,
                )
                .order_by("-intent_score", "created_at")[:batch_size]
            )

            if not leads:
                self.stdout.write(self.style.NOTICE(f"🏁 هیچ لید جدیدی (NEW) با امتیاز بالای {min_intent} برای ارسال وجود ندارد."))
                return 0

            self.stdout.write(self.style.SUCCESS(f"🎯 تعداد {len(leads)} لید با اولویت بالا برای این بچ انتخاب شدند."))

            sent_in_batch = 0

            for idx, lead in enumerate(leads):
                # Re-check circadian window
                if not is_within_circadian_hours():
                    self.stdout.write(self.style.WARNING("ساعت کاری به پایان رسید. توقف بچ."))
                    break

                # Pick sender round-robin across available senders
                sender = available_senders[idx % len(available_senders)]
                # Refresh from DB to have latest quota/status
                sender.refresh_from_db()
                if sender.daily_action_count >= max_daily_dms or sender.status != "ACTIVE":
                    self.stdout.write(self.style.WARNING(f"اکانت @{sender.username} دیگر در دسترس نیست. چرخش به اکانت بعدی..."))
                    continue

                sender_user = sender.username
                pitch_info = (
                    sender_pitches.get(sender_user)
                    or sender_pitches.get(sender_user.lower())
                    or DEFAULT_SENDER_PITCHES.get(sender_user.lower(), {})
                )
                consultant_name = pitch_info.get("consultant_name", sender_user)
                pitch_text = pitch_info.get("template", "")

                if not pitch_text:
                    # Fallback natural message if no config found
                    clean_name = (lead.full_name or lead.username or "").split()[0]
                    pitch_text = (
                        f"درود بر شما {clean_name} عزیز\n"
                        f"بنده {consultant_name} هستم مشاور و پشتیبانی تخصصی دندان‌پزشکی.\n"
                        f"در مورد کامنتتون سوالی داشتید در خدمتم."
                    )

                self.stdout.write("-" * 65)
                self.stdout.write(
                    f"👉 [مشتری {idx + 1}/{len(leads)}]: @{lead.username} (PK: {lead.ig_user_pk}) "
                    f"| احتمال خرید: {int(lead.intent_score * 100)}% | نیاز: {lead.comment_text[:40]}"
                )
                self.stdout.write(
                    f"   📌 اکانت فرستنده: @{sender_user} (مشاور: {consultant_name}) | کوتای امروز: {sender.daily_action_count + 1}/{max_daily_dms}"
                )

                if dry_run:
                    self.stdout.write(self.style.HTTP_INFO(f"   [DRY-RUN] شبیه‌سازی ارسال به @{lead.username}:"))
                    self.stdout.write(f"   متن: {pitch_text[:120]}...")
                    with transaction.atomic():
                        lead.status = "DM_SENT"
                        lead.save(update_fields=["status", "updated_at"])
                        sender.daily_action_count += 1
                        sender.last_action_at = django_tz.now()
                        sender.save(update_fields=["daily_action_count", "last_action_at"])
                    sent_in_batch += 1
                    time.sleep(2)
                    continue

                # Live execution
                try:
                    cl = get_or_create_client(sender)

                    # Step 1: Human Warm-up (Profile Visit)
                    self.stdout.write(f"   [۱/۳] 👤 شبیه‌سازی باز کردن پروفایل @{lead.username} توسط @{sender_user}...")
                    try:
                        cl.user_info_by_username(lead.username)
                    except Exception as visit_err:
                        logger.debug(f"Profile visit notice for @{lead.username}: {visit_err}")

                    # Step 2: Human Typing Delay (15 to 35 seconds)
                    typing_delay = random.uniform(15.0, 35.0)
                    self.stdout.write(f"   [۲/۳] ⌨️ شبیه‌سازی تایپ انسانی ({typing_delay:.1f} ثانیه)...")
                    time.sleep(typing_delay)

                    # Step 3: Direct Send Message
                    self.stdout.write(f"   [۳/۳] ✉️ ارسال دایرکت از طرف {consultant_name} (@{sender_user})...")
                    target_pk = int(lead.ig_user_pk) if lead.ig_user_pk and str(lead.ig_user_pk).isdigit() else None
                    if not target_pk:
                        target_pk = cl.user_id_from_username(lead.username)

                    cl.direct_send(pitch_text, user_ids=[target_pk])

                    # Success: Update DB atomically
                    with transaction.atomic():
                        lead.status = "DM_SENT"
                        lead.save(update_fields=["status", "updated_at"])
                        sender.daily_action_count += 1
                        sender.last_action_at = django_tz.now()
                        sender.status_message = "پیام با موفقیت ارسال شد"
                        sender.save(update_fields=["daily_action_count", "last_action_at", "status_message"])

                    sent_in_batch += 1
                    self.stdout.write(self.style.SUCCESS(f"   ✅ [ارسال موفق] دایرکت با موفقیت به @{lead.username} ارسال شد."))

                    # Safety delay between consecutive DMs (4 to 8 minutes)
                    if idx + 1 < len(leads):
                        safety_sleep = random.uniform(delay_min, delay_max)
                        self.stdout.write(
                            f"   ⏳ [وقفه ایمنی ضد بلاک]: {safety_sleep / 60.0:.1f} دقیقه ({int(safety_sleep)} ثانیه) تا پیام بعدی...\n"
                        )
                        time.sleep(safety_sleep)

                except Exception as exc:
                    self.stderr.write(self.style.ERROR(f"   ❌ خطا در ارسال دایرکت به @{lead.username}: {exc}"))
                    exc_str = str(exc).lower()

                    # Check for rate limit / challenge / action block
                    if "challenge" in exc_str or "checkpoint" in exc_str:
                        sender.status = "CHALLENGED"
                        sender.status_message = "نیازمند حل چالش امنیتی اینستاگرام"
                        sender.save(update_fields=["status", "status_message"])
                        clients_cache.pop(sender.username, None)
                        self.stderr.write(self.style.ERROR(f"   ⚠️ اکانت @{sender.username} وارد چالش شد. از مدار خارج شد."))
                    elif "feedback" in exc_str or "please wait" in exc_str or "rate limit" in exc_str:
                        cooldown_hours = 6
                        sender.cooldown_until = django_tz.now() + django_tz.timedelta(hours=cooldown_hours)
                        sender.status_message = f"محدودیت موقت اینستاگرام (استراحت {cooldown_hours} ساعته)"
                        sender.save(update_fields=["cooldown_until", "status_message"])
                        clients_cache.pop(sender.username, None)
                        self.stderr.write(self.style.ERROR(f"   ⚠️ اکانت @{sender.username} در حالت استراحت موقت قرار گرفت."))
                    else:
                        # Mark lead as FAILED to prevent indefinite stuck loop
                        lead.status = "FAILED"
                        lead.save(update_fields=["status", "updated_at"])

            return sent_in_batch

        # Execution controller
        if once or not loop:
            sent = execute_batch()
            self.stdout.write(self.style.SUCCESS(f"\n🏁 عملیات تک‌بچ به پایان رسید. مجموع دایرکت‌های ارسالی: {sent}"))
            return

        # Continuous loop
        self.stdout.write(self.style.SUCCESS(f"🔄 حالت اجرای پیوسته فعال شد (فاصله بررسی بچ‌ها: {interval} دقیقه)...\n"))
        while True:
            try:
                execute_batch()
            except Exception as batch_err:
                self.stderr.write(self.style.ERROR(f"خطای کلی در حلقه اجرای بچ: {batch_err}"))

            self.stdout.write(f"💤 استراحت چرخه ({interval} دقیقه تا بررسی مجدد صف لیدها)...")
            time.sleep(interval * 60)
