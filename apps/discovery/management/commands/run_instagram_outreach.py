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
from typing import Optional, Dict, Any, List, Tuple

from django.core.management.base import BaseCommand
from django.utils import timezone as django_tz
from django.db import models, transaction

try:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))

from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
from apps.discovery.anti_ban import InstagramAntiBanManager
from apps.discovery.device_emulator import apply_device_profile_to_client
from apps.discovery.outreach_service import (
    check_and_sync_lead_replies,
    get_tier_specs,
    qualify_recipient_profile,
    perform_organic_warmup,
    build_bulletproof_message,
    parse_spintax,
    execute_organic_journey,
    process_queued_outbox_message,
)
from apps.discovery.human_routine import HumanShiftPlanner, DailyShiftSchedule

logger = logging.getLogger("instagram_outreach")

CONFIG_FILE = Path("data/sarv_outreach_config.json")

DEFAULT_SENDER_PITCHES = {
    "raha.rostamim": {
        "consultant_name": "رها",
        "consultant_role": "مشاور و پشتیبانی تخصصی",
        "template": (
            "{درود بر شما|سلام وقتتون بخیر|سلام و احترام}{ عزیز|}!\n"
            "بنده رها هستم مشاور {business_name}؛ {متوجه شدم|دیدم} پیگیر {topic} بودید.\n"
            "{خوشحال می‌شم هر سوال یا ابهامی دارید راهنماییتون کنم|پاسخگوی تمام سوالات تخصصی شما در این زمینه هستم|اگر سوالی درباره هزینه‌ها، کیفیت یا شرایط دارید در خدمتم}.\n"
            "{اگر فکر می‌کنید براتون مفیده، شماره تماستون رو بگذارید تا برای یک مشاوره اختصاصی تلفنی و بدون دغدغه خدمتتون تماس بگیرم.|در صورت تمایل می‌تونید شماره تماستون رو ارسال کنید تا راهنمایی کامل تلفنی انجام بشه.|می‌تونید همینجا بفرمایید تا کامل راهنماییتون کنم.}"
        ),
    },
    "pania.mehrim": {
        "consultant_name": "پنیا",
        "consultant_role": "مشاور و پشتیبانی تخصصی",
        "template": (
            "{سلام روزتون بخیر|درود و عرض ادب|سلام و احترام}{ گرامی|}!\n"
            "پنیا هستم از تیم مشاوره تخصصی {business_name}. {مشاهده کردم|دیدم} در مورد {topic} سوال داشتید.\n"
            "{خوشحال می‌شم کمکتون کنم تا بهترین تصمیم رو بگیرید|در خدمتتون هستم برای پاسخ به سوالات یا بررسی شرایط و هزینه‌ها}.\n"
            "{در صورت تمایل شماره تماستون رو ارسال بفرمایید تا یک تماس کوتاه برای مشاوره اختصاصی باهاتون داشته باشیم.|اگر سوالی هست همینجا بفرمایید با کمال میل پاسخگو هستم.}"
        ),
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
            default=1500,
            help="Minimum delay in seconds between consecutive DMs (default: 1500s / 25m).",
        )
        parser.add_argument(
            "--delay-max",
            type=int,
            default=3000,
            help="Maximum delay in seconds between consecutive DMs (default: 3000s / 50m).",
        )
        parser.add_argument(
            "--max-daily-dms",
            type=int,
            default=9,
            help="Maximum daily DMs per sender account (default: 9 DMs in 5-6 hours).",
        )
        parser.add_argument(
            "--ignore-circadian",
            "--ignore-shift",
            action="store_true",
            dest="ignore_circadian",
            help="Bypass circadian/shift hours check (for testing outside active shift window).",
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
        self.stdout.write(self.style.SUCCESS("🚀 [موتور اختصاصی ارسال دایرکت هوشمند و ارگانیک اینستاگرام (Instagram Outreach)]"))
        self.stdout.write(self.style.SUCCESS(f"📌 هدف: مستأجر {tenant_ids} | حداقل احتمال خرید: {min_intent} | حالت تست: {dry_run}"))

        today_sched = HumanShiftPlanner.get_or_create_daily_schedule()
        self.stdout.write(self.style.SUCCESS(
            f"📅 برنامه شیفت کاری امروز ({today_sched.date}):\n"
            f"   • شیفت صبح: {today_sched.morning_start.strftime('%H:%M')} الی {today_sched.morning_end.strftime('%H:%M')}\n"
            f"   • شیفت عصر: {today_sched.afternoon_start.strftime('%H:%M')} الی {today_sched.afternoon_end.strftime('%H:%M')}\n"
            f"   • مجموع ساعت کار: {today_sched.total_work_minutes / 60.0:.1f} ساعت ({today_sched.total_work_minutes} دقیقه) | منبع: {today_sched.generated_by}"
        ))
        self.stdout.write(self.style.SUCCESS("=" * 75))

        cfg = load_outreach_config()
        sender_pitches = cfg.get("sender_pitches", {})

        clients_cache: Dict[str, Any] = {}

        def get_or_create_client(account: InstagramEngineAccount):
            if account.username in clients_cache:
                return clients_cache[account.username]

            from instagrapi import Client
            from requests.adapters import HTTPAdapter

            class TimeoutHTTPAdapter(HTTPAdapter):
                def __init__(self, connect_timeout=10, read_timeout=15, post_read_timeout=35, *args, **kwargs):
                    self.connect_timeout = connect_timeout
                    self.read_timeout = read_timeout
                    self.post_read_timeout = post_read_timeout
                    super().__init__(*args, **kwargs)

                def send(self, request, **kwargs):
                    if kwargs.get("timeout") is None:
                        rt = self.post_read_timeout if request.method == "POST" else self.read_timeout
                        kwargs["timeout"] = (self.connect_timeout, rt)
                    return super().send(request, **kwargs)

            cl = Client()
            cl.public_request_retries_count = 0
            cl.public_request_retries_timeout = 0
            cl.delay_range = [2, 5]
            clean_proxy = getattr(account, "clean_proxy", None) or account.proxy_url
            if clean_proxy:
                cl.set_proxy(clean_proxy)

            # Apply persistent unique device fingerprint BEFORE authentication
            try:
                apply_device_profile_to_client(cl, account)
            except Exception as dev_err:
                self.stderr.write(f"Warning setting device profile for @{account.username}: {dev_err}")

            adapter = TimeoutHTTPAdapter(connect_timeout=10, read_timeout=15, post_read_timeout=35)
            cl.private.mount("https://", adapter)
            cl.private.mount("http://", adapter)

            # Authenticate with pre-configured persistent device settings
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

        def check_shift_status() -> Tuple[bool, str, int]:
            if ignore_circadian:
                return True, "بررسی شیفت کاری بر اساس فلگ دستی نادیده گرفته شد.", 0
            return HumanShiftPlanner.is_shift_active()

        def is_within_circadian_hours() -> bool:
            active, _, _ = check_shift_status()
            return active

        def get_available_senders() -> List[InstagramEngineAccount]:
            now = django_tz.now()
            now_tehran = datetime.now(TEHRAN_TZ)

            # Reset daily counts if last action was on a previous day
            # Reset hourly count if last action was in a previous hour
            for acc in InstagramEngineAccount.objects.filter(role="SENDER"):
                updated = False
                update_cols = []
                if acc.last_action_at and acc.last_action_at.astimezone(TEHRAN_TZ).date() < now_tehran.date():
                    acc.daily_action_count = 0
                    update_cols.append("daily_action_count")
                    updated = True

                if acc.last_hour_reset and acc.last_hour_reset.astimezone(TEHRAN_TZ).hour != now_tehran.hour:
                    acc.hourly_action_count = 0
                    acc.last_hour_reset = now
                    update_cols.extend(["hourly_action_count", "last_hour_reset"])
                    updated = True
                elif not acc.last_hour_reset:
                    acc.last_hour_reset = now
                    update_cols.append("last_hour_reset")
                    updated = True

                if updated:
                    acc.save(update_fields=list(set(update_cols)))

            senders = InstagramEngineAccount.objects.filter(
                role="SENDER",
                status="ACTIVE",
                tenant_id__in=tenant_ids,
            ).filter(
                models.Q(cooldown_until__isnull=True) | models.Q(cooldown_until__lte=now)
            )

            # Check both daily limit and hourly rate strictly enforced by Account Tier specs
            available = []
            for s in senders:
                is_valid_role, _ = InstagramAntiBanManager.validate_account_role(s, "SENDER")
                if not is_valid_role:
                    continue

                # Anti-Ban Tier Guardrail: Enforces warm-up quotas based on account age
                tier_spec = get_tier_specs(s.account_tier)
                limit_daily = min(s.daily_limit or tier_spec["default_daily"], tier_spec["max_daily"])
                limit_hourly = min(s.hourly_limit or tier_spec["hourly_speed"], tier_spec["hourly_speed"])

                if s.daily_action_count < limit_daily and (s.hourly_action_count or 0) < limit_hourly:
                    available.append(s)

            # Order by least recently used
            available.sort(key=lambda s: s.last_action_at or datetime.min.replace(tzinfo=timezone.utc))
            return available

        sender_consecutive_errors: Dict[str, int] = {}
        sender_action_counts: Dict[str, int] = {}

        def _sync_replies():
            # 📬 Automatic Reply Checking (Runs after DM sending so it never blocks outbound messages)
            try:
                reply_res = check_and_sync_lead_replies(tenant_ids)
                if reply_res.get("replies_found", 0) > 0:
                    self.stdout.write(self.style.SUCCESS(f"   🎉 تعداد {reply_res['replies_found']} پاسخ جدید از مشتریان دریافت شد!"))
                    for d in reply_res.get("details", []):
                        self.stdout.write(f"      • @{d['lead']}: \"{d['reply_text']}\"")
            except Exception as reply_err:
                self.stderr.write(f"   هشدار در بررسی پاسخ‌ها: {reply_err}")

        def execute_batch() -> int:
            is_active, shift_reason, wait_sec = check_shift_status()
            if not is_active:
                now_tehran_str = datetime.now(TEHRAN_TZ).strftime("%H:%M")
                self.stdout.write(self.style.WARNING(
                    f"💤 [SHIFT PAUSE] ساعت محلی تهران {now_tehran_str} است. وضعیت: {shift_reason}"
                ))
                return 0

            available_senders = get_available_senders()
            if not available_senders:
                self.stdout.write(self.style.WARNING("⚠️ هیچ اکانت فرستنده (SENDER) با ظرفیت خالی در حال حاضر در دسترس نیست."))
                return 0

            self.stdout.write(f"👥 اکانت‌های فرستنده در دسترس: {[f'@{s.username} (امروز: {s.daily_action_count}/{s.daily_limit or 18} | این ساعت: {s.hourly_action_count or 0}/{s.hourly_limit or 3})' for s in available_senders]}")

            sent_in_batch = 0

            # -------------------------------------------------------------
            # 📬 PRIORITY 1: Secure Outbox Queue (Follow-ups & Operator DMs)
            # -------------------------------------------------------------
            queued_leads = list(
                InstagramEngineLead.objects.filter(
                    tenant_id__in=tenant_ids,
                    status="QUEUED",
                ).order_by("updated_at")[:batch_size]
            )

            if queued_leads:
                self.stdout.write(self.style.SUCCESS(
                    f"⚡ [اولویت ۱ - صف ارسال مستقیم]: تعداد {len(queued_leads)} پیام در صف اولویت یافت شد."
                ))
                for q_lead in queued_leads:
                    if sent_in_batch >= batch_size:
                        break
                    if not is_within_circadian_hours():
                        self.stdout.write(self.style.WARNING("ساعت کاری به پایان رسید. توقف صف."))
                        break

                    self.stdout.write(f"📨 ارسال پیام صف برای @{q_lead.username}...")
                    res = process_queued_outbox_message(q_lead, client_cache=clients_cache, dry_run=dry_run)
                    if res.get("success"):
                        sent_in_batch += 1
                        sender_name = res.get("sender") or "مشاور"
                        sender_action_counts[sender_name] = sender_action_counts.get(sender_name, 0) + 1
                        self.stdout.write(self.style.SUCCESS(
                            f"   ✅ پیام صف با موفقیت برای @{q_lead.username} توسط @{sender_name} ارسال شد."
                        ))
                        delay_sec, _ = HumanShiftPlanner.calculate_next_action_delay(is_priority_reply=True)
                        if not dry_run and sent_in_batch < batch_size:
                            self.stdout.write(f"   ⏳ مکث کوتاه مکالمه اولویت‌دار ({delay_sec:.1f} ثانیه)...\n")
                            time.sleep(delay_sec)
                        elif dry_run:
                            time.sleep(1)
                    else:
                        self.stderr.write(self.style.ERROR(
                            f"   ❌ خطا در ارسال پیام صف برای @{q_lead.username}: {res.get('error')}"
                        ))

            # If quota for this batch was filled by queued messages, skip cold leads
            if sent_in_batch >= batch_size:
                self.stdout.write(self.style.SUCCESS(f"🎉 سقف ارسال این چرخه ({batch_size} پیام) از طریق صف اولویت تکمیل شد."))
                _sync_replies()
                return sent_in_batch

            # -------------------------------------------------------------
            # 🎯 PRIORITY 2: Cold Lead Direct Outreach (High-intent Leads)
            # -------------------------------------------------------------
            remaining_slots = batch_size - sent_in_batch
            candidate_pool = max(remaining_slots * 20, 50)
            leads = list(
                InstagramEngineLead.objects.filter(
                    tenant_id__in=tenant_ids,
                    status="NEW",
                    intent_score__gte=min_intent,
                )
                .order_by("-intent_score", "created_at")[:candidate_pool]
            )

            if not leads:
                if sent_in_batch == 0:
                    self.stdout.write(self.style.NOTICE(f"🏁 هیچ لید جدیدی (NEW) با امتیاز بالای {min_intent} یا پیام در صف برای ارسال وجود ندارد."))
                _sync_replies()
                return sent_in_batch

            self.stdout.write(self.style.SUCCESS(f"🎯 تعداد {len(leads)} لید کاندیدا انتخاب شدند (هدف ارسال باقی‌مانده در این چرخه: {remaining_slots} دایرکت)."))

            sent_in_batch = 0

            for idx, lead in enumerate(leads):
                if sent_in_batch >= batch_size:
                    self.stdout.write(self.style.SUCCESS(f"🎉 سقف ارسال این چرخه ({batch_size} پیام) با موفقیت تکمیل شد."))
                    break

                # Re-check circadian window
                if not is_within_circadian_hours():
                    self.stdout.write(self.style.WARNING("ساعت کاری به پایان رسید. توقف بچ."))
                    break

                # Pick active sender with remaining daily and hourly quota
                chosen_sender = None
                candidates = available_senders[idx % len(available_senders):] + available_senders[:idx % len(available_senders)]
                for cand in candidates:
                    cand.refresh_from_db()
                    cand_daily_limit = cand.daily_limit or max_daily_dms or 18
                    cand_hourly_limit = cand.hourly_limit or 3
                    if (
                        cand.status == "ACTIVE"
                        and cand.daily_action_count < cand_daily_limit
                        and (cand.hourly_action_count or 0) < cand_hourly_limit
                        and (not cand.cooldown_until or cand.cooldown_until <= django_tz.now())
                    ):
                        chosen_sender = cand
                        break

                if not chosen_sender:
                    self.stdout.write(self.style.WARNING("⚠️ هیچ اکانت فرستنده‌ای در این لحظه دارای کوتای ارسال خالی نیست. پایان بچ."))
                    break

                sender = chosen_sender
                is_valid_role, role_err = InstagramAntiBanManager.validate_account_role(sender, "SENDER")
                if not is_valid_role:
                    self.stderr.write(self.style.ERROR(role_err))
                    continue
                sender_user = sender.username
                pitch_info = (
                    sender_pitches.get(sender_user)
                    or sender_pitches.get(sender_user.lower())
                    or DEFAULT_SENDER_PITCHES.get(sender_user.lower(), {})
                )
                consultant_name = pitch_info.get("consultant_name", sender_user)
                raw_template = pitch_info.get("template", "")

                biz_name = "مجموعه تخصصی سرو"
                if hasattr(lead, "business") and lead.business and getattr(lead.business, "name", None):
                    biz_name = lead.business.name

                # 🛡️ Anti-Spam Shield: generate 100% unique Spintax text with tailored topic
                pitch_text = build_bulletproof_message(
                    pitch_template=raw_template,
                    lead=lead,
                    consultant_name=consultant_name,
                    business_name=biz_name,
                )

                tier_specs = get_tier_specs(sender.account_tier)
                limit_display = min(sender.daily_limit or tier_specs["default_daily"], tier_specs["max_daily"])
                self.stdout.write("-" * 65)
                self.stdout.write(
                    f"👉 [مشتری {idx + 1}/{len(leads)}]: @{lead.username} (PK: {lead.ig_user_pk}) "
                    f"| احتمال خرید: {int(lead.intent_score * 100)}% | نیاز: {lead.comment_text[:40]}"
                )
                self.stdout.write(
                    f"   📌 اکانت فرستنده: @{sender_user} ({tier_specs['title']}) | کوتای امروز: {sender.daily_action_count + 1}/{limit_display}"
                )

                if dry_run:
                    self.stdout.write(self.style.HTTP_INFO(f"   [DRY-RUN] شبیه‌سازی ارسال به @{lead.username}:"))
                    self.stdout.write(f"   متن: {pitch_text[:120]}...")
                    with transaction.atomic():
                        lead.status = "DM_SENT"
                        lead.sender_account = sender_user
                        lead.initial_dm_text = pitch_text
                        lead.initial_dm_at = django_tz.now()
                        lead.conversation_history = [{
                            "sender": sender_user,
                            "direction": "out",
                            "role": "initial_outreach",
                            "text": pitch_text,
                            "time": django_tz.now().isoformat(),
                            "delivery_status": "SENT",
                        }]
                        lead.save(update_fields=["status", "sender_account", "initial_dm_text", "initial_dm_at", "conversation_history", "updated_at"])
                        sender.daily_action_count += 1
                        sender.hourly_action_count = (sender.hourly_action_count or 0) + 1
                        sender.last_action_at = django_tz.now()
                        sender.save(update_fields=["daily_action_count", "hourly_action_count", "last_action_at"])
                    sent_in_batch += 1
                    sender_action_counts[sender.username] = sender_action_counts.get(sender.username, 0) + 1
                    time.sleep(1)
                    continue

                # Live execution with Super Shield
                try:
                    # 🛡️ Step 0: Mobile Proxy Verification & Automatic IP Rotation
                    from apps.discovery.proxy_service import prepare_sender_proxy_before_dm
                    prepare_sender_proxy_before_dm(sender, stdout=self.stdout)

                    cl = get_or_create_client(sender)

                    # 🛡️ Step 1: Pre-Flight Safety Qualification
                    self.stdout.write(f"   [۱/۳] 🔍 بررسی ایمنی اکانت مقصد @{lead.username} توسط گاردریل...")
                    qualify = qualify_recipient_profile(cl, lead.username, target_pk=lead.ig_user_pk)
                    if not qualify["eligible"]:
                        self.stdout.write(self.style.WARNING(
                            f"   ⏭️ [سپر ضد بلاک]: ارسال به @{lead.username} لغو شد: {qualify['reason']}"
                        ))
                        lead.status = "SKIPPED"
                        lead.comment_text = (lead.comment_text or "") + f"\n[گاردریل ضد بلاک]: {qualify['reason']}"
                        lead.save(update_fields=["status", "comment_text", "updated_at"])
                        continue

                    target_pk = qualify.get("target_pk") or (int(lead.ig_user_pk) if lead.ig_user_pk and str(lead.ig_user_pk).isdigit() else None)
                    if not target_pk:
                        self.stdout.write(self.style.WARNING(f"   ⚠️ عدم امکان تعیین PK معتبر برای @{lead.username}. رد شد."))
                        lead.status = "SKIPPED"
                        lead.save(update_fields=["status", "updated_at"])
                        continue

                    # 🛡️ Step 2: Full In-App Organic Navigation Journey
                    self.stdout.write(f"   [۲/۳] 🌐 پیمایش ارگانیک و تعامل طبیعی در اینستاگرام برای @{lead.username}...")
                    journey_res = execute_organic_journey(cl, lead.username, target_pk=target_pk)
                    actions = journey_res.get("actions", {})
                    if actions.get("feed_scrolled"):
                        self.stdout.write("      📱 اسکرول فید تایم‌لاین انجام شد.")
                    if actions.get("feed_post_liked"):
                        self.stdout.write("      👍 یک پست تصادفی در فید لایک شد.")
                    if actions.get("user_searched"):
                        self.stdout.write("      🔎 جستجوی نام کاربری در سرچ‌بار اینستاگرام انجام شد.")
                    if actions.get("profile_viewed"):
                        self.stdout.write("      👤 صفحه پروفایل و گرید پست‌های مخاطب بازدید شد.")
                    if actions.get("story_viewed"):
                        self.stdout.write("      👁️ استوری فعال مخاطب مشاهده شد.")
                    if actions.get("target_post_liked"):
                        self.stdout.write("      ❤️ پست اخیر مخاطب لایک شد (اعلان در گوشی کاربر).")
                    self.stdout.write(f"      ⌨️ وقفه تایپ طبیعی ({actions.get('typing_delay_sec', 20)} ثانیه) انجام شد.")

                    final_pk = journey_res.get("target_pk") or target_pk

                    # 🛡️ Step 3: Direct Send Message
                    self.stdout.write(f"   [۳/۳] ✉️ ارسال دایرکت ضد اسپم از طرف {consultant_name} (@{sender_user})...")
                    cl.direct_send(pitch_text, user_ids=[final_pk])

                    # Success: Update DB atomically
                    now = django_tz.now()
                    with transaction.atomic():
                        lead.status = "DM_SENT"
                        lead.sender_account = sender_user
                        lead.initial_dm_text = pitch_text
                        lead.initial_dm_at = now
                        lead.conversation_history = [{
                            "sender": sender_user,
                            "direction": "out",
                            "role": "initial_outreach",
                            "text": pitch_text,
                            "time": now.isoformat(),
                            "delivery_status": "SENT",
                        }]
                        lead.save(update_fields=[
                            "status", "sender_account", "initial_dm_text", "initial_dm_at",
                            "conversation_history", "updated_at"
                        ])
                        sender.daily_action_count += 1
                        sender.hourly_action_count = (sender.hourly_action_count or 0) + 1
                        sender.last_action_at = now
                        sender.status_message = "پیام با موفقیت ارسال شد"
                        sender.save(update_fields=["daily_action_count", "hourly_action_count", "last_action_at", "status_message"])

                    # Reset consecutive errors on successful send
                    sender_consecutive_errors[sender.username] = 0
                    sender_action_counts[sender.username] = sender_action_counts.get(sender.username, 0) + 1
                    sent_in_batch += 1
                    self.stdout.write(self.style.SUCCESS(f"   ✅ [ارسال موفق و ایمن] دایرکت با موفقیت به @{lead.username} ارسال شد."))

                    # Dynamic Safety delay with stochastic micro-breaks
                    if idx + 1 < len(leads) and sent_in_batch < batch_size:
                        calc_delay, is_micro_break = HumanShiftPlanner.calculate_next_action_delay(
                            account_tier=sender.account_tier,
                            consecutive_actions=sender_action_counts[sender.username],
                            is_priority_reply=False,
                        )
                        if is_micro_break:
                            self.stdout.write(self.style.WARNING(
                                f"   ☕ [استراحت طبیعی / قهوه یا مکالمه تلفنی]: توقف {calc_delay / 60.0:.1f} دقیقه ({int(calc_delay)} ثانیه) تا اقدام بعدی...\n"
                            ))
                        else:
                            tier_specs = get_tier_specs(sender.account_tier)
                            self.stdout.write(
                                f"   ⏳ [وقفه ایمنی طبیعی بر اساس رده {tier_specs['title']}]: {calc_delay / 60.0:.1f} دقیقه ({int(calc_delay)} ثانیه) تا پیام بعدی...\n"
                            )
                        time.sleep(calc_delay)

                except Exception as exc:
                    self.stderr.write(self.style.ERROR(f"   ❌ خطا در ارسال دایرکت به @{lead.username}: {exc}"))
                    exc_str = str(exc).lower()

                    consec_err = sender_consecutive_errors.get(sender.username, 0) + 1
                    sender_consecutive_errors[sender.username] = consec_err

                    # Check for rate limit / challenge / action block
                    if "challenge" in exc_str or "checkpoint" in exc_str or "suspended" in exc_str:
                        sender.status = "CHALLENGED"
                        sender.status_message = "نیازمند حل چالش امنیتی اینستاگرام"
                        sender.save(update_fields=["status", "status_message"])
                        clients_cache.pop(sender.username, None)
                        if sender in available_senders:
                            available_senders.remove(sender)
                        self.stderr.write(self.style.ERROR(f"   ⚠️ اکانت @{sender.username} وارد چالش شد. از مدار خارج شد."))
                        lead.status = "NEW"
                        lead.save(update_fields=["status", "updated_at"])
                    elif (
                        "login_required" in exc_str
                        or "loginrequired" in exc_str
                        or "user_has_logged_out" in exc_str
                        or "unauthorized" in exc_str
                        or "clientforbiddenerror" in exc_str
                    ):
                        sender.status = "CHALLENGED"
                        sender.status_message = "سشن اینستاگرام منقضی شده است (نیاز به لاگین مجدد یا ثبت کوکی sessionid جدید)"
                        sender.save(update_fields=["status", "status_message"])
                        clients_cache.pop(sender.username, None)
                        if sender in available_senders:
                            available_senders.remove(sender)
                        self.stderr.write(self.style.ERROR(f"   ⚠️ سشن اکانت @{sender.username} منقضی شده است (LoginRequired). از مدار خارج شد."))
                        lead.status = "NEW"
                        lead.save(update_fields=["status", "updated_at"])
                    elif (
                        "connection refused" in exc_str
                        or "connectionerror" in exc_str
                        or "proxyconnectionerror" in exc_str
                        or "errno 111" in exc_str
                        or "max retries exceeded" in exc_str
                    ):
                        cooldown_minutes = 15
                        # If mobile proxy has rotation URL, attempt immediate IP rotation to recover connection
                        if getattr(sender, "proxy_rotation_url", None):
                            from apps.discovery.proxy_service import rotate_account_proxy_ip
                            self.stdout.write("   🔄 چرخش خودکار IP پروکسی موبایل جهت رفع خطای اتصال...")
                            rotate_account_proxy_ip(sender)

                        sender.cooldown_until = django_tz.now() + django_tz.timedelta(minutes=cooldown_minutes)
                        sender.status = "COOLDOWN"
                        sender.status_message = f"خطای اتصال پروکسی ({exc_str[:50]}) - استراحت {cooldown_minutes} دقیقه‌ای"
                        sender.save(update_fields=["cooldown_until", "status", "status_message"])
                        clients_cache.pop(sender.username, None)
                        if sender in available_senders:
                            available_senders.remove(sender)
                        self.stderr.write(self.style.ERROR(f"   ⚠️ خطای پروکسی یا اتصال برای @{sender.username}. به مدت {cooldown_minutes} دقیقه استراحت داده شد."))
                        lead.status = "NEW"
                        lead.save(update_fields=["status", "updated_at"])
                    elif "1404006" in exc_str:
                        # Error 1404006: Meta's temporary action block on this sender sending DMs to non-followers
                        self.stderr.write(self.style.ERROR(
                            f"   ⚠️ [محدودیت دایرکت 1404006]: اکانت @{sender.username} دارای محدودیت ارسال دایرکت به غیرفالوور است. انتقال به استراحت و تعویض اکانت."
                        ))
                        cooldown_hours = 3
                        sender.cooldown_until = django_tz.now() + django_tz.timedelta(hours=cooldown_hours)
                        sender.status = "COOLDOWN"
                        sender.status_message = f"محدودیت موقت دایرکت (1404006) - استراحت {cooldown_hours} ساعته"
                        sender.save(update_fields=["cooldown_until", "status", "status_message"])
                        clients_cache.pop(sender.username, None)
                        if sender in available_senders:
                            available_senders.remove(sender)
                        # Lead remains NEW so another active sender can deliver it
                        lead.status = "NEW"
                        lead.save(update_fields=["status", "updated_at"])
                    elif "message controls" in exc_str or "privacy" in exc_str:
                        self.stdout.write(self.style.WARNING(
                            f"   🛡️ [حریم خصوصی مشتری]: مخاطب @{lead.username} تنظیمات پیام را مسدود کرده است (رد شد)."
                        ))
                        lead.status = "SKIPPED"
                        lead.comment_text = (lead.comment_text or "") + "\n[محدودیت کاربر]: مسدود بودن دایرکت اکانت‌های ناشناس"
                        lead.save(update_fields=["status", "comment_text", "updated_at"])
                    elif "user not found" in exc_str or "usernotfound" in exc_str or "not_found" in exc_str:
                        lead.status = "SKIPPED"
                        lead.comment_text = (lead.comment_text or "") + "\n[کاربر یافت نشد]: پیج تغییر نام داده یا حذف شده است"
                        lead.save(update_fields=["status", "comment_text", "updated_at"])
                    elif "feedback" in exc_str or "please wait" in exc_str or "rate limit" in exc_str or "403" in exc_str:
                        cooldown_hours = 3
                        sender.cooldown_until = django_tz.now() + django_tz.timedelta(hours=cooldown_hours)
                        sender.status = "COOLDOWN"
                        sender.status_message = f"محدودیت موقت اینستاگرام (استراحت {cooldown_hours} ساعته)"
                        sender.save(update_fields=["cooldown_until", "status", "status_message"])
                        clients_cache.pop(sender.username, None)
                        if sender in available_senders:
                            available_senders.remove(sender)
                        self.stderr.write(self.style.ERROR(f"   ⚠️ اکانت @{sender.username} در حالت استراحت موقت قرار گرفت."))
                        lead.status = "NEW"
                        lead.save(update_fields=["status", "updated_at"])
                    else:
                        # For unhandled exceptions, do NOT mark lead as FAILED if sender might be the issue
                        if consec_err >= 3:
                            cooldown_hours = 1
                            sender.cooldown_until = django_tz.now() + django_tz.timedelta(hours=cooldown_hours)
                            sender.status = "COOLDOWN"
                            sender.status_message = f"خطاهای متوالی ({exc_str[:50]}) - استراحت {cooldown_hours} ساعته"
                            sender.save(update_fields=["cooldown_until", "status", "status_message"])
                            clients_cache.pop(sender.username, None)
                            if sender in available_senders:
                                available_senders.remove(sender)
                        lead.status = "NEW"
                        lead.save(update_fields=["status", "updated_at"])

            _sync_replies()
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
                is_active, shift_reason, wait_sec = check_shift_status()
                if not is_active:
                    now_tehran_str = datetime.now(TEHRAN_TZ).strftime("%H:%M")
                    self.stdout.write(self.style.WARNING(
                        f"💤 [SHIFT PAUSE] ساعت محلی تهران {now_tehran_str} است. وضعیت: {shift_reason}"
                    ))
                    pause = min(wait_sec, 300)
                    self.stdout.write(f"⏳ مکث تا آغاز یا ادامه شیفت کاری ({pause // 60} دقیقه دیگر)...")
                    time.sleep(pause)
                    continue

                sent = execute_batch()
            except Exception as batch_err:
                self.stderr.write(self.style.ERROR(f"خطای کلی در حلقه اجرای بچ: {batch_err}"))
                sent = 0

            if sent and sent > 0:
                self.stdout.write(f"💤 استراحت چرخه ({interval} دقیقه پس از ارسال موفق تا بررسی مجدد)...")
                time.sleep(interval * 60)
            else:
                short_pause = 45
                self.stdout.write(f"⏳ غربالگری صف ادامه دارد (مکث کوتاه {short_pause} ثانیه‌ای)...")
                time.sleep(short_pause)
