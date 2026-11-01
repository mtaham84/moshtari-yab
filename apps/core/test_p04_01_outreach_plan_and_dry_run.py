import io
from datetime import datetime, timedelta, timezone as dt_timezone
from django.test import TransactionTestCase
from django.core.management import call_command
from django.utils import timezone

from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
from apps.discovery.outreach_service import plan_outreach_batch

try:
    from zoneinfo import ZoneInfo
    TEHRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    TEHRAN_TZ = dt_timezone(timedelta(hours=3, minutes=30))


class OutreachPlanAndDryRunTestCase(TransactionTestCase):
    def setUp(self):
        self.tenant_id = "tenant_test_p04"

        # Create active sender account
        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_id,
            username="test.sender",
            role="SENDER",
            status="ACTIVE",
            daily_limit=15,
            hourly_limit=4,
            daily_action_count=2,
            hourly_action_count=1,
            last_action_at=timezone.now(),
        )

        # Create high-intent NEW leads
        self.lead1 = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            ig_user_pk="ig_1001",
            username="buyer_one",
            comment_text="قیمت این محصول چنده و چطور ارسال میشه؟",
            intent_score=0.92,
            status="NEW",
        )
        self.lead2 = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            ig_user_pk="ig_1002",
            username="buyer_two",
            comment_text="لطفاً راهنمایی بفرمایید می‌خوام ثبت سفارش کنم",
            intent_score=0.88,
            status="NEW",
        )

    def test_plan_outreach_batch_returns_plan_without_mutating_db(self):
        """plan_outreach_batch returns full planning decisions with ZERO database mutations."""
        clock_inside_window = datetime(2026, 11, 1, 14, 0, tzinfo=TEHRAN_TZ)

        plan = plan_outreach_batch(
            tenant_ids=[self.tenant_id],
            batch_size=5,
            min_intent=0.70,
            clock=clock_inside_window,
        )

        self.assertTrue(plan["eligible"])
        self.assertEqual(plan["reason"], "OK")
        self.assertEqual(plan["leads_count"], 2)
        self.assertEqual(len(plan["planned_actions"]), 2)

        action1 = plan["planned_actions"][0]
        self.assertEqual(action1["username"], "buyer_one")
        self.assertEqual(action1["sender_username"], "test.sender")
        self.assertTrue(len(action1["pitch_text"]) > 10)

        # Assert DB is 100% untouched
        self.lead1.refresh_from_db()
        self.lead2.refresh_from_db()
        self.assertEqual(self.lead1.status, "NEW")
        self.assertEqual(self.lead2.status, "NEW")
        self.assertEqual(self.lead1.initial_dm_text, "")
        self.assertIsNone(self.lead1.initial_dm_at)

        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 2)
        self.assertEqual(self.sender.hourly_action_count, 1)

    def test_circadian_window_enforcement_in_planner(self):
        """Planner enforces 09:00 - 23:00 Tehran circadian rhythm when not bypassed."""
        # 03:30 AM Tehran time (outside circadian window)
        clock_night = datetime(2026, 11, 1, 3, 30, tzinfo=TEHRAN_TZ)
        plan_night = plan_outreach_batch(
            tenant_ids=[self.tenant_id],
            clock=clock_night,
            ignore_circadian=False,
        )
        self.assertFalse(plan_night["eligible"])
        self.assertEqual(plan_night["reason"], "CIRCADIAN_PAUSE")
        self.assertEqual(len(plan_night["planned_actions"]), 0)

        # With ignore_circadian=True, it proceeds
        plan_bypassed = plan_outreach_batch(
            tenant_ids=[self.tenant_id],
            clock=clock_night,
            ignore_circadian=True,
        )
        self.assertTrue(plan_bypassed["eligible"])
        self.assertEqual(len(plan_bypassed["planned_actions"]), 2)

    def test_expired_counter_simulation_in_memory_without_db_write(self):
        """Senders with counts from previous day calculate effective=0 without mutating DB."""
        yesterday = timezone.now() - timedelta(days=1)
        self.sender.daily_action_count = 15  # At limit
        self.sender.last_action_at = yesterday
        self.sender.save(update_fields=["daily_action_count", "last_action_at"])

        clock_today = timezone.now()
        plan = plan_outreach_batch(
            tenant_ids=[self.tenant_id],
            clock=clock_today,
            ignore_circadian=True,
        )

        self.assertTrue(plan["eligible"])
        self.assertEqual(len(plan["planned_actions"]), 2)

        # In-memory effective count was 0, but DB was NOT mutated by planner
        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 15)

    def test_command_dry_run_executes_purely_as_simulation(self):
        """run_instagram_outreach command with --dry-run prints simulation and leaves DB untouched."""
        out = io.StringIO()
        call_command(
            "run_instagram_outreach",
            "--tenant", self.tenant_id,
            "--dry-run",
            "--once",
            "--ignore-circadian",
            stdout=out,
        )

        output = out.getvalue()
        self.assertIn("[PLANNING & DRY-RUN]", output)
        self.assertIn("شبیه‌سازی شد", output)
        self.assertIn("@buyer_one", output)
        self.assertIn("@buyer_two", output)

        # Strict assertion: no status changes or counter changes
        self.lead1.refresh_from_db()
        self.lead2.refresh_from_db()
        self.assertEqual(self.lead1.status, "NEW")
        self.assertEqual(self.lead2.status, "NEW")

        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 2)
        self.assertEqual(self.sender.hourly_action_count, 1)
