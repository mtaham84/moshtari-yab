"""
Unit tests for Human Behavioral Routine, Organic Navigation Journey, and Dispatch Queue.
"""
import datetime
from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.discovery.human_routine import DailyShiftSchedule, HumanShiftPlanner, TEHRAN_TZ
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
from apps.discovery.outreach_service import (
    enqueue_direct_message,
    execute_organic_journey,
    get_lead_conversation_thread,
    process_queued_outbox_message,
)


class HumanRoutineAndQueueTests(TestCase):
    def setUp(self):
        self.tenant_id = "tenant_test"
        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_id,
            username="raha_test",
            role="SENDER",
            status="ACTIVE",
            account_tier="AGED",
            daily_limit=20,
            hourly_limit=5,
            daily_action_count=0,
            hourly_action_count=0,
            session_json={"cookies": {"sessionid": "mock_session_123"}},
        )
        self.lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            username="client_test",
            ig_user_pk="987654321",
            status="NEW",
            intent_score=0.92,
            comment_text="سلام قیمت کامپوزیت چنده؟",
        )

    def test_daily_shift_schedule_bounds(self):
        """Test that generated schedules always fall strictly between 360 and 420 minutes."""
        today = datetime.date(2026, 11, 5)
        sched = HumanShiftPlanner.generate_daily_schedule(today, use_llm=False)

        self.assertIsInstance(sched, DailyShiftSchedule)
        self.assertGreaterEqual(sched.total_work_minutes, 360)
        self.assertLessEqual(sched.total_work_minutes, 420)
        self.assertLess(sched.morning_start, sched.morning_end)
        self.assertLess(sched.morning_end, sched.afternoon_start)
        self.assertLess(sched.afternoon_start, sched.afternoon_end)

        sched_dict = sched.to_dict()
        self.assertIn("total_work_hours", sched_dict)
        self.assertGreaterEqual(sched_dict["total_work_hours"], 6.0)
        self.assertLessEqual(sched_dict["total_work_hours"], 7.0)

    def test_is_shift_active_evaluations(self):
        """Test active and inactive shift intervals."""
        target_date = datetime.date(2026, 11, 5)
        custom_sched = DailyShiftSchedule(
            date=target_date,
            morning_start=datetime.time(10, 0),
            morning_end=datetime.time(13, 30),
            afternoon_start=datetime.time(16, 30),
            afternoon_end=datetime.time(20, 0),
            total_work_minutes=420,
            generated_by="test",
        )

        # 1. During morning shift (11:00)
        dt_morning = datetime.datetime(2026, 11, 5, 11, 0, tzinfo=TEHRAN_TZ)
        active, reason, wait_sec = HumanShiftPlanner.is_shift_active(dt_morning, schedule=custom_sched)
        self.assertTrue(active)
        self.assertEqual(wait_sec, 0)
        self.assertIn("شیفت صبح", reason)

        # 2. During lunch break (14:30)
        dt_lunch = datetime.datetime(2026, 11, 5, 14, 30, tzinfo=TEHRAN_TZ)
        active, reason, wait_sec = HumanShiftPlanner.is_shift_active(dt_lunch, schedule=custom_sched)
        self.assertFalse(active)
        self.assertGreater(wait_sec, 0)
        self.assertIn("ناهار", reason)

        # 3. During afternoon shift (17:15)
        dt_afternoon = datetime.datetime(2026, 11, 5, 17, 15, tzinfo=TEHRAN_TZ)
        active, reason, wait_sec = HumanShiftPlanner.is_shift_active(dt_afternoon, schedule=custom_sched)
        self.assertTrue(active)
        self.assertEqual(wait_sec, 0)
        self.assertIn("شیفت عصر", reason)

        # 4. Deep night / after shift (22:00)
        dt_night = datetime.datetime(2026, 11, 5, 22, 0, tzinfo=TEHRAN_TZ)
        active, reason, wait_sec = HumanShiftPlanner.is_shift_active(dt_night, schedule=custom_sched)
        self.assertFalse(active)
        self.assertGreater(wait_sec, 0)
        self.assertIn("پایان شیفت‌های کاری امروز", reason)

    def test_calculate_next_action_delay(self):
        """Test delay calculation for priority replies and cold outreach micro-breaks."""
        # Priority reply delay
        delay, is_break = HumanShiftPlanner.calculate_next_action_delay(is_priority_reply=True)
        self.assertFalse(is_break)
        self.assertGreaterEqual(delay, 60.0)
        self.assertLessEqual(delay, 135.0)

        # Normal cold action delay for AGED tier
        delay_cold, is_break_cold = HumanShiftPlanner.calculate_next_action_delay(
            account_tier="AGED", consecutive_actions=1, is_priority_reply=False
        )
        self.assertFalse(is_break_cold)
        self.assertGreater(delay_cold, 120.0)

        # Micro-break triggered after 12 consecutive actions (multiple of 3 and 4)
        delay_break, is_break_true = HumanShiftPlanner.calculate_next_action_delay(
            account_tier="AGED", consecutive_actions=12, is_priority_reply=False
        )
        self.assertTrue(is_break_true)
        self.assertGreaterEqual(delay_break, 1500.0)  # At least 25 minutes

    def test_enqueue_direct_message(self):
        """Test enqueuing messages into the secure dispatch queue."""
        res = enqueue_direct_message(
            lead_id=self.lead.id,
            message_text="سلام دوست عزیز، وقت بخیر!",
            sender_username="raha_test",
            priority="HIGH",
        )
        self.assertTrue(res["success"])
        self.assertTrue(res["queued"])

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "QUEUED")
        self.assertEqual(len(self.lead.conversation_history), 1)

        msg = self.lead.conversation_history[0]
        self.assertEqual(msg["sender"], "raha_test")
        self.assertEqual(msg["text"], "سلام دوست عزیز، وقت بخیر!")
        self.assertEqual(msg["delivery_status"], "QUEUED")

        # Test thread formatting reflects queued status
        thread = get_lead_conversation_thread(self.lead)
        self.assertEqual(len(thread), 1)
        self.assertTrue(thread[0]["is_queued"])
        self.assertEqual(thread[0]["delivery_status"], "QUEUED")

    def test_process_queued_outbox_message_dry_run(self):
        """Test dry-run processing of queued outbox message."""
        enqueue_direct_message(
            lead_id=self.lead.id,
            message_text="پیام تستی صف در حالت آزمایشی",
            sender_username="raha_test",
        )
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "QUEUED")

        res = process_queued_outbox_message(self.lead, dry_run=True)
        self.assertTrue(res["success"])
        self.assertTrue(res["dry_run"])

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "FOLLOWUP_SENT")
        self.assertEqual(self.lead.second_message_text, "پیام تستی صف در حالت آزمایشی")

        # Verify conversation history marked as SENT
        item = self.lead.conversation_history[0]
        self.assertEqual(item["delivery_status"], "SENT")
        self.assertIn("sent_at", item)

    @patch("time.sleep", return_value=None)
    @patch("apps.discovery.device_emulator.apply_device_profile_to_client")
    def test_process_queued_outbox_message_live(self, mock_device, mock_sleep):
        """Test live execution of queued message with mock client."""
        enqueue_direct_message(
            lead_id=self.lead.id,
            message_text="سلام، هزینه کامپوزیت واحدی ۲.۵ میلیون است.",
            sender_username="raha_test",
        )
        self.lead.refresh_from_db()

        mock_cl = MagicMock()
        mock_cl.direct_send.return_value = True

        client_cache = {"raha_test": mock_cl}
        with patch("apps.discovery.outreach_service.execute_organic_journey") as mock_journey:
            mock_journey.return_value = {"target_pk": 987654321, "actions": {}}
            res = process_queued_outbox_message(self.lead, client_cache=client_cache, dry_run=False)

        self.assertTrue(res["success"])
        mock_cl.direct_send.assert_called_once_with(
            "سلام، هزینه کامپوزیت واحدی ۲.۵ میلیون است.",
            user_ids=[987654321],
        )

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "FOLLOWUP_SENT")
        self.assertEqual(self.lead.conversation_history[0]["delivery_status"], "SENT")

    @patch("time.sleep", return_value=None)
    def test_execute_organic_journey(self, mock_sleep):
        """Test full in-app organic navigation steps simulation."""
        mock_cl = MagicMock()
        mock_cl.timeline_feed.return_value = [MagicMock(id="media_1")]
        mock_cl.search_users.return_value = [MagicMock(pk="987654321", username="client_test")]
        mock_cl.user_medias.return_value = [MagicMock(id="post_1")]

        with patch("apps.discovery.outreach_service.perform_organic_warmup") as mock_warmup, \
                patch("apps.discovery.outreach_service.random.random", return_value=0.0):  # every optional step
            mock_warmup.return_value = {"liked_post": True, "viewed_story": True}
            res = execute_organic_journey(mock_cl, "client_test", target_pk=987654321)

        self.assertEqual(res["target_pk"], 987654321)
        actions = res["actions"]
        self.assertTrue(actions["feed_scrolled"])
        self.assertTrue(actions["user_searched"])
        self.assertTrue(actions["profile_viewed"])
        self.assertTrue(actions["target_post_liked"])
        self.assertTrue(actions["story_viewed"])
        self.assertGreater(actions["typing_delay_sec"], 0)

    @patch("time.sleep", return_value=None)
    def test_run_instagram_outreach_command_execution(self, mock_sleep):
        """Test management command execution in dry-run mode handling both QUEUED and NEW leads."""
        from io import StringIO
        from django.core.management import call_command

        # 1. Create a queued lead
        queued_lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            username="queued_client",
            ig_user_pk="111222333",
            status="QUEUED",
            intent_score=0.95,
            conversation_history=[{
                "sender": "raha_test",
                "direction": "out",
                "role": "operator_message",
                "text": "درود، پاسخ به سوال شما درباره نوبت دهی.",
                "delivery_status": "QUEUED",
            }],
        )

        out = StringIO()
        call_command(
            "run_instagram_outreach",
            tenant=self.tenant_id,
            dry_run=True,
            once=True,
            ignore_circadian=True,
            batch_size=2,
            stdout=out,
        )

        output_str = out.getvalue()
        self.assertIn("موتور اختصاصی ارسال دایرکت هوشمند و ارگانیک", output_str)
        self.assertIn("برنامه شیفت کاری امروز", output_str)
        self.assertIn("اولویت ۱ - صف ارسال مستقیم", output_str)
        self.assertIn("queued_client", output_str)
        self.assertIn("client_test", output_str)

        # Verify queued lead was processed
        queued_lead.refresh_from_db()
        self.assertEqual(queued_lead.status, "FOLLOWUP_SENT")
        self.assertEqual(queued_lead.conversation_history[0]["delivery_status"], "SENT")

        # Verify cold lead was processed
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "DM_SENT")
        self.assertEqual(self.lead.conversation_history[0]["delivery_status"], "SENT")

