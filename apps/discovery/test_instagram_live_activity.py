from django.contrib.auth.models import User
from django.test import Client, TestCase
from apps.discovery.models import (
    InstagramEngineAccount,
    InstagramLiveActivity,
    InstagramLiveWorkerState,
)
from apps.discovery.live_logger import (
    report_live_activity,
    get_live_monitoring_snapshot,
    simulate_test_pulse,
)


class InstagramLiveActivityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("admin_tester", "admin@test.local", "pass123")
        self.client = Client()
        self.client.force_login(self.user)

        self.account = InstagramEngineAccount.objects.create(
            username="scraper_bot_1",
            role="SCRAPER",
            status="ACTIVE",
        )

    def test_report_live_activity_and_worker_state(self):
        """Test reporting an activity updates both the log and worker heartbeat."""
        log_id = report_live_activity(
            account_username="scraper_bot_1",
            action_type="WATCH_VIDEO",
            title="تماشای ریلز اینستاگرام C1234",
            description="مشاهده ۵ ثانیه از ویدیو",
            media_type="REEL",
            media_code="C1234",
            media_url="https://www.instagram.com/p/C1234/",
            duration_seconds=5.2,
            level="INFO",
            update_worker="scraper",
            worker_active=True,
        )
        self.assertIsNotNone(log_id)

        # Check activity entry
        act = InstagramLiveActivity.objects.get(id=log_id)
        self.assertEqual(act.action_type, "WATCH_VIDEO")
        self.assertEqual(act.media_code, "C1234")
        self.assertEqual(act.media_type, "REEL")
        self.assertEqual(act.duration_seconds, 5.2)

        # Check worker state
        worker = InstagramLiveWorkerState.objects.get(worker_key="scraper")
        self.assertTrue(worker.is_active)
        self.assertEqual(worker.current_account, "scraper_bot_1")
        self.assertEqual(worker.action_type, "WATCH_VIDEO")
        self.assertEqual(worker.media_code, "C1234")

    def test_get_live_monitoring_snapshot(self):
        """Test snapshot retrieval formats logs and states accurately."""
        report_live_activity(
            account_username="sender_bot_1",
            action_type="SEND_DM",
            title="ارسال دایرکت به @buyer1",
            level="SUCCESS",
            update_worker="outreach",
            worker_active=True,
        )

        data = get_live_monitoring_snapshot(limit=10)
        self.assertTrue(data["success"])
        self.assertIn("scraper_state", data)
        self.assertIn("outreach_state", data)
        self.assertTrue(data["outreach_state"]["is_active"])
        self.assertEqual(data["outreach_state"]["account"], "sender_bot_1")
        self.assertGreaterEqual(len(data["logs"]), 1)
        self.assertEqual(data["logs"][0]["action_type"], "SEND_DM")

    def test_simulate_test_pulse(self):
        """Test triggering a simulated pulse creates valid reel watch logs."""
        res = simulate_test_pulse(account_username="scraper_bot_1")
        self.assertTrue(res["success"])
        self.assertIn("media_code", res)

        # Verify a WATCH_VIDEO record exists
        recent = InstagramLiveActivity.objects.filter(action_type="WATCH_VIDEO").first()
        self.assertIsNotNone(recent)
        self.assertEqual(recent.media_type, "REEL")

    def test_live_logs_api_endpoints(self):
        """Test the AJAX API endpoints."""
        # 1. Get logs API
        resp = self.client.get("/manage/instagram/live-logs/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])

        # 2. Test pulse API
        resp_pulse = self.client.post("/manage/instagram/live-logs/test-pulse/", {"username": "scraper_bot_1"})
        self.assertEqual(resp_pulse.status_code, 200)
        self.assertTrue(resp_pulse.json()["success"])

        # 3. Clear logs API
        resp_clear = self.client.post("/manage/instagram/live-logs/clear/")
        self.assertEqual(resp_clear.status_code, 200)
        self.assertEqual(InstagramLiveActivity.objects.count(), 0)
