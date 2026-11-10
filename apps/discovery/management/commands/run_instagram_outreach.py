"""
ig-outreach runner for the single Instagram DM dispatcher (apps.discovery.outreach_engine).

The sending logic (queued messages first, then the best 45-point lead, organic journey, guardrails,
per-account pacing, tier caps and per-sender error handling) lives in the engine and is shared with the
Celery task. This command only ticks it about once a minute and checks replies every random 5-10 minutes
during shift hours.

Use it when INSTAGRAM_OUTREACH_DISPATCHER=command (default); with "celery" use celery-beat instead.
"""

import logging
import random
import time
from datetime import datetime

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import models

from apps.discovery.human_routine import HumanShiftPlanner
from apps.discovery.models import InstagramEngineLead
from apps.discovery.outreach_engine import (  # noqa: F401  (re-exported for older imports and tests)
    CONFIG_FILE,
    DEFAULT_SENDER_PITCHES,
    PROXY_ERROR_MARKERS,
    SESSION_EXPIRED_MARKERS,
    TEHRAN_TZ,
    _error_matches,
    load_outreach_config,
    next_reply_sync_delay,
    reply_sync_allowed,
    run_outreach_batch,
)
from apps.discovery.outreach.replies import check_and_sync_lead_replies, serve_reply_syncs

logger = logging.getLogger("instagram_outreach")

WAIT_LABELS = {
    "global_gap": "فاصله عمومی بین دایرکت‌های سرد",
    "sender_pacing": "فاصله انسانی اکانت‌های فرستنده",
}


class Command(BaseCommand):
    help = "Run the Instagram DM dispatcher (queued messages + 45-point cold outreach) with SENDER accounts."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=str, default=None,
                            help="Tenant ID or keyword (e.g. 'tenant_4' or 'sarv'). Default: OUTREACH_TENANTS, "
                                 "else every tenant with an ACTIVE SENDER account.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Simulate sends without Instagram calls and without pacing. WARNING: it still "
                                 "marks leads as sent in the database, never use it on the production DB.")
        parser.add_argument("--once", action="store_true", help="Run one batch and exit.")
        parser.add_argument("--loop", action="store_true", help="Run continuously.")
        parser.add_argument("--batch-size", type=int, default=2,
                            help="Max messages per tick (default: 2). Pacing usually allows one cold DM.")
        parser.add_argument("--interval", type=int, default=None,
                            help="Min minutes between two cold DMs across all senders "
                                 "(default: INSTAGRAM_OUTREACH_MIN_GAP_MINUTES).")
        parser.add_argument("--tick-seconds", type=int, default=60, help="Seconds between ticks in loop mode.")
        parser.add_argument("--reply-sync-seconds", type=int, default=None,
                            help="Fixed seconds between inbox reply checks in loop mode (min 60). Default: random "
                                 "INSTAGRAM_REPLY_SYNC_MIN_SECONDS..MAX_SECONDS (5-10 min). Only during shift hours.")
        parser.add_argument("--max-daily-dms", type=int, default=9,
                            help="Daily DM cap for senders without their own limit (never above the tier cap).")
        parser.add_argument("--min-intent", type=float, default=None,
                            help="Deprecated, ignored: ranking uses the 45-point outreach score.")
        parser.add_argument("--delay-min", type=int, default=None,
                            help="Deprecated, ignored: per-account delays come from the warm-up tier.")
        parser.add_argument("--delay-max", type=int, default=None,
                            help="Deprecated, ignored: per-account delays come from the warm-up tier.")
        parser.add_argument("--ignore-circadian", "--ignore-shift", action="store_true", dest="ignore_circadian",
                            help="Bypass the daily shift hours check.")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Single-dispatcher guard: never run in parallel with the Celery outreach dispatcher
        dispatcher = getattr(settings, "INSTAGRAM_OUTREACH_DISPATCHER", "command")
        if dispatcher != "command" and not dry_run:
            raise CommandError(
                f"INSTAGRAM_OUTREACH_DISPATCHER={dispatcher}: Celery is the active DM dispatcher. "
                "Stop/remove the ig-outreach service (or use --dry-run) to avoid double-sending."
            )

        from apps.discovery.tenants import outreach_tenants, tenant_ids as ig_tenant_ids
        def tenant_groups():
            # re-resolved every tick without --tenant: senders activated later are picked up without a restart
            tenants = [options["tenant"]] if options["tenant"] else outreach_tenants()
            return [ig_tenant_ids(t) for t in tenants]   # each includes legacy aliases such as "sarv"

        groups = tenant_groups()
        tenant_ids = [tid for group in groups for tid in group]
        if not groups:
            self.stdout.write(self.style.WARNING(
                "⚠️ هیچ مستأجری برای ارسال نیست (OUTREACH_TENANTS خالی و اکانت SENDER فعالی وجود ندارد)."))
        min_gap = options["interval"] if options["interval"] is not None else settings.INSTAGRAM_OUTREACH_MIN_GAP_MINUTES

        self.stdout.write(self.style.SUCCESS("=" * 75))
        self.stdout.write(self.style.SUCCESS("🚀 [موتور اختصاصی ارسال دایرکت هوشمند و ارگانیک اینستاگرام (Instagram Outreach)]"))
        self.stdout.write(self.style.SUCCESS(
            f"📌 هدف: مستأجر {tenant_ids} | رتبه‌بندی: امتیاز ۴۵تایی | فاصله عمومی دایرکت سرد: {min_gap} دقیقه | حالت تست: {dry_run}"
        ))
        sched = HumanShiftPlanner.get_or_create_daily_schedule()
        self.stdout.write(self.style.SUCCESS(
            f"📅 برنامه شیفت کاری امروز ({sched.date}):\n"
            f"   • شیفت صبح: {sched.morning_start.strftime('%H:%M')} الی {sched.morning_end.strftime('%H:%M')}\n"
            f"   • شیفت عصر: {sched.afternoon_start.strftime('%H:%M')} الی {sched.afternoon_end.strftime('%H:%M')}\n"
            f"   • مجموع ساعت کار: {sched.total_work_minutes / 60.0:.1f} ساعت | منبع: {sched.generated_by}"
        ))
        self.stdout.write(self.style.SUCCESS("=" * 75))

        self._startup_recovery(tenant_ids)

        step_kwargs = {
            "dry_run": dry_run,
            "ignore_shift": options["ignore_circadian"],
            "max_daily_dms": options["max_daily_dms"],
            "min_gap_minutes": min_gap,
            "log": self.stdout.write,
            "stdout": self.stdout,
        }
        batch_size = max(1, options["batch_size"])

        def run_batches():
            nonlocal groups, tenant_ids
            groups = tenant_groups()
            tenant_ids = [tid for group in groups for tid in group]
            cfg = load_outreach_config()   # re-read every tick so pitch edits apply without a restart
            results = [run_outreach_batch(group, max_sends=batch_size, cfg=cfg, **step_kwargs) for group in groups]
            for res in results:
                self._report(res["last"])
            paused = [r["last"] for r in results if r["last"].get("action") == "paused"]
            return {"sent": sum(r["sent"] for r in results),
                    "paused": paused[0] if paused and len(paused) == len(results) else None}

        if options["once"] or not options["loop"]:
            batch = run_batches()
            if not dry_run and reply_sync_allowed(options["ignore_circadian"])[0]:
                self._sync_replies(groups, ignore_shift=options["ignore_circadian"])
            self.stdout.write(self.style.SUCCESS(f"\n🏁 عملیات تک‌بچ به پایان رسید. مجموع پیام‌های ارسالی: {batch['sent']}"))
            return

        tick = max(15, options["tick_seconds"])
        fixed_reply_every = max(60, options["reply_sync_seconds"]) if options["reply_sync_seconds"] else None
        next_reply_sync = 0.0
        self.stdout.write(self.style.SUCCESS(f"🔄 حالت پیوسته فعال شد (هر {tick} ثانیه یک بررسی؛ فاصله‌ها در دیتابیس)."))
        while True:
            pause = tick
            try:
                batch = run_batches()
                if batch["paused"]:
                    pause = max(tick, min(int(batch["paused"].get("wait_seconds") or tick), 300))
                if not dry_run and reply_sync_allowed(options["ignore_circadian"])[0]:  # no inbox checks outside the shift
                    due_all = time.monotonic() >= next_reply_sync
                    self._sync_replies(groups, requested_only=not due_all, ignore_shift=options["ignore_circadian"])
                    if due_all:
                        next_reply_sync = time.monotonic() + (fixed_reply_every or next_reply_sync_delay())
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"خطای کلی در حلقه ارسال: {exc}"))
                logger.exception("ig-outreach tick failed")
            time.sleep(pause + random.uniform(0, tick * 0.5))

    def _startup_recovery(self, tenant_ids):
        # Unblock leads that were skipped only because their profile was private (private DMs are allowed now)
        try:
            unblocked = InstagramEngineLead.objects.filter(tenant_id__in=tenant_ids, status="SKIPPED").filter(
                models.Q(comment_text__contains="پروفایل خصوصی") | models.Q(comment_text__contains="Private")
            ).update(status="NEW")
            if unblocked:
                self.stdout.write(self.style.SUCCESS(f"♻️ [بازیابی خودکار]: {unblocked} لید پرایوت به چرخه ارسال برگشت."))
        except Exception as exc:
            logger.debug("Failed unblocking private leads: %s", exc, exc_info=True)
        # Sync and restore sender accounts from the pool file
        try:
            from apps.discovery import accounts_pool_file
            logger.info("Accounts pool checked at startup: %s", accounts_pool_file.import_pool(force=False))
        except Exception as exc:
            logger.debug("Pool file sync skipped: %s", exc, exc_info=True)

    def _report(self, last):
        action = last.get("action")
        if action == "paused":
            now_str = datetime.now(TEHRAN_TZ).strftime("%H:%M")
            self.stdout.write(self.style.WARNING(f"💤 [SHIFT PAUSE] ساعت تهران {now_str}: {last.get('reason')}"))
        elif action == "waiting":
            next_at = last.get("next_at")
            when = next_at.astimezone(TEHRAN_TZ).strftime("%H:%M") if next_at else "نامشخص"
            self.stdout.write(f"⏳ در انتظار {WAIT_LABELS.get(last.get('reason'), last.get('reason'))} (تا حدود {when}).")
        elif action == "no_senders":
            self.stdout.write(self.style.WARNING("⚠️ هیچ اکانت فرستنده (SENDER) با ظرفیت خالی در دسترس نیست."))
        elif action == "idle":
            self.stdout.write("هیچ لید جدیدی (NEW) در بازه ۵ روز اخیر برای ارسال وجود ندارد.")
        elif action == "busy":
            self.stdout.write("مرحله قبلی هنوز در حال اجراست؛ این دور رد شد.")

    def _sync_replies(self, groups, *, requested_only: bool = False, ignore_shift: bool = False):
        if not groups:
            return
        try:
            results = serve_reply_syncs(
                groups,
                requested_only=requested_only,
                ignore_shift=ignore_shift,
                sync_fn=check_and_sync_lead_replies,
            )
            for res in results:
                if res.get("replies_found", 0) > 0:
                    self.stdout.write(self.style.SUCCESS(f"   🎉 تعداد {res['replies_found']} پاسخ جدید از مشتریان دریافت شد!"))
                    for d in res.get("details", []):
                        self.stdout.write(f"      • @{d['lead']}: \"{d['reply_text']}\"")
        except Exception as exc:
            logger.warning("Reply sync failed", exc_info=True)
            self.stderr.write(f"   هشدار در بررسی پاسخ‌ها: {exc}")
