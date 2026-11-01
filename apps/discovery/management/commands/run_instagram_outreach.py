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
import os
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
from apps.discovery.outreach_service import (
    check_and_sync_lead_replies,
    get_tier_specs,
    qualify_recipient_profile,
    perform_organic_warmup,
    build_bulletproof_message,
    parse_spintax,
    plan_outreach_batch,
)

logger = logging.getLogger("instagram_outreach")

CONFIG_FILE = Path("data/sarv_outreach_config.json")

DEFAULT_SENDER_PITCHES = {
    "raha.rostamim": {
        "consultant_name": "رها",
        "consultant_role": "مشاور و پشتیبانی تخصصی",
        "template": "درود بر شما \nبنده رها هستم مشاور و پشتیبانی تخصصی {business_name} ، متوجه شدم پیگیر امور {topic} بودید\nمیتونید هر سوالی دارید از بنده بپرسید. \nدر صورت تمایل شماره تماس بگذارید تا برای مشاوره تخصصی خدمتتون تماس بگیریم.",
    },
    "pania.mehrim": {
        "consultant_name": "پنیا",
        "consultant_role": "مشاور و پشتیبانی تخصصی",
        "template": "درود بر شما \nبنده پنیا هستم مشاور و پشتیبانی تخصصی {business_name} ، متوجه شدم پیگیر امور {topic} بودید\nمیتونید هر سوالی دارید از بنده بپرسید \nدر صورت تمایل شماره تماس بگذارید تا برای مشاوره تخصصی خدمتتون تماس بگیریم.",
    },
}


def load_outreach_config(tenant_id: str | None = None) -> dict:
    pitches = dict(DEFAULT_SENDER_PITCHES)
    cfg = {}
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            for u, p in cfg.get("sender_pitches", {}).items():
                pitches[u.lower()] = p
        except Exception as exc:
            logger.warning(f"Could not load {CONFIG_FILE}: {exc}")

    # Merge tenant-isolated pitches if tenant_id is provided
    if tenant_id and "tenant_pitches" in cfg:
        tenant_p = cfg["tenant_pitches"].get(tenant_id, {})
        for u, p in tenant_p.items():
            pitches[u.lower()] = p

    cfg["sender_pitches"] = pitches
    return cfg


class Command(BaseCommand):
    help = "Run automated Instagram Direct Outreach for high-intent leads using SENDER accounts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant",
            type=str,
            default=os.environ.get("IG_OUTREACH_TENANT", ""),
            help="Tenant ID or keyword. Default: from IG_OUTREACH_TENANT env or required.",
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
            action="store_true",
            help="Bypass circadian hours check (for testing outside 09:00 - 23:00).",
        )
        parser.add_argument(
            "--sync-replies",
            action="store_true",
            help="Explicitly sync replies from customers before live outreach batch.",
        )

    def handle(self, *args, **options):
        tenant = (options.get("tenant") or "").strip()
        if not tenant:
            self.stdout.write(self.style.WARNING("⚠️ هیچ شناسه‌ای برای مستأجر (--tenant یا IG_OUTREACH_TENANT) تعیین نشده است. اجرای ورکر متوقف گردید."))
            return

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

        primary_tenant = tenant_ids[0] if tenant_ids else None
        cfg = load_outreach_config(tenant_id=primary_tenant)
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
                cooldown_until__isnull=True
            ) | InstagramEngineAccount.objects.filter(
                role="SENDER",
                status="ACTIVE",
                tenant_id__in=tenant_ids,
                cooldown_until__lte=now,
            )

            # Check both daily limit and hourly rate according to Dealism benchmarks
            available = []
            for s in senders:
                limit_daily = s.daily_limit or max_daily_dms or 18
                limit_hourly = s.hourly_limit or 3
                if s.daily_action_count < limit_daily and (s.hourly_action_count or 0) < limit_hourly:
                    available.append(s)

            # Order by least recently used
            available.sort(key=lambda s: s.last_action_at or datetime.min.replace(tzinfo=timezone.utc))
            return available

        sender_consecutive_errors: Dict[str, int] = {}

        sync_replies = options.get("sync_replies", False)

        def execute_batch() -> int:
            if dry_run:
                self.stdout.write("🔍 [PLANNING & DRY-RUN]: برنامه‌ریزی ارسال بدون ایجاد اثر جانبی یا فراخوانی شبکه...")
                plan = plan_outreach_batch(
                    tenant_ids=tenant_ids,
                    batch_size=batch_size,
                    min_intent=min_intent,
                    max_daily_dms=max_daily_dms,
                    sender_pitches=sender_pitches,
                    ignore_circadian=ignore_circadian,
                )
                if not plan["eligible"]:
                    if plan["reason"] == "CIRCADIAN_PAUSE":
                        self.stdout.write(self.style.WARNING(
                            f"💤 [CIRCADIAN PAUSE] ساعت محلی تهران {plan.get('current_time_tehran')} است. ارسال فقط در ساعات ۹:۰۰ تا ۲۳:۰۰ مجاز است."
                        ))
                    elif plan["reason"] == "NO_AVAILABLE_SENDERS":
                        self.stdout.write(self.style.WARNING("⚠️ هیچ اکانت فرستنده (SENDER) با ظرفیت خالی در حال حاضر در دسترس نیست."))
                    return 0

                actions = plan["planned_actions"]
                if not actions:
                    self.stdout.write(self.style.NOTICE(f"🏁 هیچ لید جدیدی (NEW) با امتیاز بالای {min_intent} برای ارسال وجود ندارد."))
                    return 0

                self.stdout.write(self.style.SUCCESS(f"🎯 تعداد {len(actions)} اقدام ارسال شبیه‌سازی شد (بدون تغییر دیتابیس یا ارتباط خارجی):"))
                for idx, act in enumerate(actions):
                    self.stdout.write("-" * 65)
                    self.stdout.write(
                        f"👉 [طرح ارسال {idx + 1}/{len(actions)}]: @{act['username']} (PK: {act['ig_user_pk']}) "
                        f"| احتمال خرید: {int(act['intent_score'] * 100)}%"
                    )
                    self.stdout.write(
                        f"   📌 اکانت انتخابی: @{act['sender_username']} (مشاور: {act['consultant_name']}) "
                        f"| سهمیه مصرفی قبل: {act['effective_daily_before']} روزانه / {act['effective_hourly_before']} ساعتی"
                    )
                    self.stdout.write(f"   ✉️ متن پیام تولیدشده: {act['pitch_text'][:120]}...")
                self.stdout.write(self.style.SUCCESS(f"\n[DRY-RUN] پایان شبیه‌سازی: {len(actions)} پیام آماده ارسال."))
                return len(actions)

            if not is_within_circadian_hours():
                now_tehran_str = datetime.now(TEHRAN_TZ).strftime("%H:%M")
                self.stdout.write(self.style.WARNING(
                    f"💤 [CIRCADIAN PAUSE] ساعت محلی تهران {now_tehran_str} است. ارسال فقط در ساعات ۹:۰۰ تا ۲۳:۰۰ مجاز است."
                ))
                return 0

            # 1. Automatic Reply Checking (only when explicit or running live)
            if sync_replies or not loop:
                self.stdout.write("📬 [پایش دایرکت‌ها]: بررسی پاسخ‌های دریافتی از مشتریان...")
                try:
                    reply_res = check_and_sync_lead_replies(tenant_ids)
                    if reply_res.get("replies_found", 0) > 0:
                        self.stdout.write(self.style.SUCCESS(f"   🎉 تعداد {reply_res['replies_found']} پاسخ جدید از مشتریان دریافت شد!"))
                        for d in reply_res.get("details", []):
                            self.stdout.write(f"      • @{d['lead']}: \"{d['reply_text']}\"")
                    else:
                        self.stdout.write("   صندوق دایرکت‌ها بررسی شد (پاسخ جدیدی ثبت نشده است).")
                except Exception as reply_err:
                    self.stderr.write(f"   هشدار در بررسی پاسخ‌ها: {reply_err}")

            available_senders = get_available_senders()
            if not available_senders:
                self.stdout.write(self.style.WARNING("⚠️ هیچ اکانت فرستنده (SENDER) با ظرفیت خالی در حال حاضر در دسترس نیست."))
                return 0

            self.stdout.write(f"👥 اکانت‌های فرستنده در دسترس: {[f'@{s.username} (امروز: {s.daily_action_count}/{s.daily_limit or 18} | این ساعت: {s.hourly_action_count or 0}/{s.hourly_limit or 3})' for s in available_senders]}")

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
                sender_user = sender.username
                pitch_info = (
                    sender_pitches.get(sender_user)
                    or sender_pitches.get(sender_user.lower())
                    or DEFAULT_SENDER_PITCHES.get(sender_user.lower(), {})
                )
                consultant_name = pitch_info.get("consultant_name", sender_user)
                raw_template = pitch_info.get("template", "")

                # 🛡️ Anti-Spam Shield: generate 100% unique Spintax text with tailored topic
                pitch_text = build_bulletproof_message(
                    pitch_template=raw_template,
                    lead=lead,
                    consultant_name=consultant_name,
                    business_name="مجموعه تخصصی سرو",
                )

                limit_display = sender.daily_limit or max_daily_dms or 18
                self.stdout.write("-" * 65)
                self.stdout.write(
                    f"👉 [مشتری {idx + 1}/{len(leads)}]: @{lead.username} (PK: {lead.ig_user_pk}) "
                    f"| احتمال خرید: {int(lead.intent_score * 100)}% | نیاز: {lead.comment_text[:40]}"
                )
                self.stdout.write(
                    f"   📌 اکانت فرستنده: @{sender_user} (مشاور: {consultant_name}) | کوتای امروز: {sender.daily_action_count + 1}/{limit_display}"
                )

                if dry_run:
                    self.stdout.write(self.style.HTTP_INFO(f"   [DRY-RUN] شبیه‌سازی ارسال به @{lead.username}:"))
                    self.stdout.write(f"   متن: {pitch_text[:120]}...")
                    # Containment Gate (R10): Dry run must be purely read-only simulation.
                    # Do NOT mutate lead status or increment sender action counters in DB.
                    sent_in_batch += 1
                    time.sleep(2)
                    continue

                # Live execution with Super Shield
                try:
                    cl = get_or_create_client(sender)

                    # 🛡️ Step 1: Pre-Flight Safety Qualification
                    self.stdout.write(f"   [۱/۴] 🔍 بررسی ایمنی اکانت مقصد @{lead.username} توسط گاردریل...")
                    qualify = qualify_recipient_profile(cl, lead.username)
                    if not qualify["eligible"]:
                        self.stdout.write(self.style.WARNING(
                            f"   ⏭️ [سپر ضد بلاک]: ارسال به @{lead.username} لغو شد: {qualify['reason']}"
                        ))
                        lead.status = "SKIPPED"
                        lead.comment_text = (lead.comment_text or "") + f"\n[گاردریل ضد بلاک]: {qualify['reason']}"
                        lead.save(update_fields=["status", "comment_text", "updated_at"])
                        continue

                    target_pk = qualify.get("target_pk")
                    if not target_pk:
                        target_pk = int(lead.ig_user_pk) if lead.ig_user_pk and str(lead.ig_user_pk).isdigit() else cl.user_id_from_username(lead.username)

                    # 🛡️ Step 2: Organic Human Warm-Up (Like recent post + View story)
                    self.stdout.write(f"   [۲/۴] ❤️ تعامل طبیعی انسانی با پیج @{lead.username}...")
                    warmup_res = perform_organic_warmup(cl, target_pk, lead.username)
                    if warmup_res.get("liked_post"):
                        self.stdout.write("      ❤️ لایک پست اخیر مخاطب انجام شد (ارسال اعلان در گوشی کاربر).")
                    if warmup_res.get("viewed_story"):
                        self.stdout.write("      👁️ استوری فعال مخاطب مشاهده شد.")

                    # 🛡️ Step 3: Human Typing Jitter
                    typing_delay = random.uniform(15.0, 32.0)
                    self.stdout.write(f"   [۳/۴] ⌨️ شبیه‌سازی تایپ انسانی ({typing_delay:.1f} ثانیه)...")
                    time.sleep(typing_delay)

                    # 🛡️ Step 4: Direct Send Message
                    self.stdout.write(f"   [۴/۴] ✉️ ارسال دایرکت ضد اسپم از طرف {consultant_name} (@{sender_user})...")
                    cl.direct_send(pitch_text, user_ids=[target_pk])

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
                    sent_in_batch += 1
                    self.stdout.write(self.style.SUCCESS(f"   ✅ [ارسال موفق و ایمن] دایرکت با موفقیت به @{lead.username} ارسال شد."))

                    # Dynamic Safety delay according to Dealism benchmarks
                    if idx + 1 < len(leads):
                        tier_specs = get_tier_specs(sender.account_tier)
                        calc_min = tier_specs.get("min_delay_sec", delay_min)
                        calc_max = tier_specs.get("max_delay_sec", delay_max)
                        safety_sleep = random.uniform(calc_min, calc_max)
                        self.stdout.write(
                            f"   ⏳ [وقفه ایمنی بر اساس رده {tier_specs['title']}]: {safety_sleep / 60.0:.1f} دقیقه ({int(safety_sleep)} ثانیه) تا پیام بعدی...\n"
                        )
                        time.sleep(safety_sleep)

                except Exception as exc:
                    self.stderr.write(self.style.ERROR(f"   ❌ خطا در ارسال دایرکت به @{lead.username}: {exc}"))
                    exc_str = str(exc).lower()

                    consec_err = sender_consecutive_errors.get(sender.username, 0) + 1
                    sender_consecutive_errors[sender.username] = consec_err

                    # Check for rate limit / challenge / action block
                    if "challenge" in exc_str or "checkpoint" in exc_str:
                        sender.status = "CHALLENGED"
                        sender.status_message = "نیازمند حل چالش امنیتی اینستاگرام"
                        sender.save(update_fields=["status", "status_message"])
                        clients_cache.pop(sender.username, None)
                        self.stderr.write(self.style.ERROR(f"   ⚠️ اکانت @{sender.username} وارد چالش شد. از مدار خارج شد."))
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
                    elif "message controls" in exc_str:
                        self.stdout.write(self.style.WARNING(
                            f"   🛡️ [حریم خصوصی مشتری]: مخاطب @{lead.username} تنظیمات پیام را مسدود کرده است (رد شد)."
                        ))
                        lead.status = "SKIPPED"
                        lead.comment_text = (lead.comment_text or "") + "\n[محدودیت کاربر]: مسدود بودن دایرکت اکانت‌های ناشناس"
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
