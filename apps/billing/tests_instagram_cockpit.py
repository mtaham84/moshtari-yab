from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from apps.discovery.models import InstagramEngineAccount
from apps.businesses.models import Business


class InstagramCockpitViewTests(TestCase):
    def setUp(self):
        # Create business and user
        self.user = User.objects.create_superuser(
            username="admin_test",
            email="admin@test.com",
            password="password123",
            is_staff=True,
            is_superuser=True,
        )
        self.business = Business.objects.create(
            name="دندان‌پزشکی تست",
            user=self.user,
        )
        self.account = InstagramEngineAccount.objects.create(
            username="test_dentist",
            role="SENDER",
            status="ACTIVE",
            tenant_id=f"tenant_{self.business.id}",
            daily_limit=18,
            session_json={"cookies": {"sessionid": "dummy_sess_123"}},
        )
        self.client = Client()

    def test_cockpit_view_requires_login(self):
        url = reverse("manage:instagram_account_cockpit", kwargs={"pk": self.account.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/accounts/login/", res.url)

    def test_cockpit_view_renders_authenticated(self):
        self.client.force_login(self.user)
        url = reverse("manage:instagram_account_cockpit", kwargs={"pk": self.account.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "@test_dentist")
        self.assertContains(res, "دندان‌پزشکی تست")
        self.assertContains(res, "صندوق دایرکت")
        self.assertContains(res, "فید تایم‌لاین")

    def test_manage_dashboard_instagram_hub_tab_renders(self):
        self.client.force_login(self.user)
        url = reverse("manage:dashboard") + "?tab=instagram-hub"
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "tab-instagram-hub")
        self.assertContains(res, "@test_dentist")
        self.assertContains(res, "ورود به کابین اکانت")

    def test_safe_encoder_handles_pydantic_http_url(self):
        from apps.billing.manage_views import SafeCockpitJSONEncoder
        import json

        class MockHttpUrl:
            def __str__(self):
                return "https://instagram.fna.fbcdn.net/v/t51.2885-19/avatar.jpg"

        data = {"profile_pic_url": MockHttpUrl(), "items": [MockHttpUrl()]}
        serialized = json.dumps(data, cls=SafeCockpitJSONEncoder)
        self.assertIn("https://instagram.fna.fbcdn.net", serialized)


class InstagramCockpitSendCounterTests(TestCase):
    """A sent DM must be counted (regression: datetime.timezone.now crashed after sending)."""

    def setUp(self):
        self.user = User.objects.create_superuser(username="admin_send", email="send@test.com", password="pw")
        self.business = Business.objects.create(name="تست ارسال", user=self.user)
        self.account = InstagramEngineAccount.objects.create(
            username="sender_acc", role="SENDER", status="ACTIVE",
            tenant_id=f"tenant_{self.business.id}", daily_limit=18,
            session_json={"cookies": {"sessionid": "dummy"}},
        )
        self.client = Client()
        self.client.force_login(self.user)
        from unittest.mock import patch

        window = patch("apps.discovery.anti_ban.in_operating_window", return_value=True)
        window.start()
        self.addCleanup(window.stop)

    def test_send_direct_updates_counter(self):
        from unittest.mock import MagicMock, patch

        fake = MagicMock()
        with patch("apps.billing.manage_views._get_cockpit_client", return_value=(fake, None)):
            res = self.client.post(
                reverse("manage:instagram_cockpit_send_direct", args=[self.account.pk]),
                {"thread_id": "123", "text": "سلام"},
            )
        self.assertEqual(res.status_code, 200, res.content)
        fake.direct_send.assert_called_once()
        self.account.refresh_from_db()
        self.assertEqual(self.account.daily_action_count, 1)
        self.assertIsNotNone(self.account.last_action_at)

    def test_send_direct_rejects_non_numeric_user_id(self):
        from unittest.mock import MagicMock, patch

        fake = MagicMock()
        with patch("apps.billing.manage_views._get_cockpit_client", return_value=(fake, None)):
            res = self.client.post(
                reverse("manage:instagram_cockpit_send_direct", args=[self.account.pk]),
                {"user_id": "abc", "text": "سلام"},
            )
        self.assertEqual(res.status_code, 400)
        fake.direct_send.assert_not_called()

    def test_organic_dm_updates_counter(self):
        from unittest.mock import MagicMock, patch

        fake = MagicMock()
        with patch("apps.billing.manage_views._get_cockpit_client", return_value=(fake, None)), \
                patch("apps.discovery.outreach.journey.execute_organic_journey", return_value={"target_pk": 1}):
            res = self.client.post(
                reverse("manage:instagram_cockpit_organic_dm", args=[self.account.pk]),
                {"target_username": "someone", "message_text": "سلام"},
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.account.refresh_from_db()
        self.assertEqual(self.account.daily_action_count, 1)

    # ADM-11: the cockpit obeys the same safety rules as automated outreach.
    def _post_send(self, data):
        from unittest.mock import MagicMock, patch

        fake = MagicMock()
        with patch("apps.billing.manage_views._get_cockpit_client", return_value=(fake, None)):
            res = self.client.post(reverse("manage:instagram_cockpit_send_direct", args=[self.account.pk]), data)
        return res, fake

    def test_daily_cap_reached_blocks_send(self):
        from apps.discovery.outreach.limits import get_effective_sender_limits

        cap, _ = get_effective_sender_limits(self.account)
        self.account.daily_action_count = cap
        self.account.save(update_fields=["daily_action_count"])
        res, fake = self._post_send({"thread_id": "123", "text": "سلام"})
        self.assertEqual(res.status_code, 429)
        fake.direct_send.assert_not_called()

    def test_cooldown_and_settle_period_block_send(self):
        from datetime import timedelta

        from django.utils import timezone

        self.account.next_outreach_at = timezone.now() + timedelta(minutes=30)
        self.account.save(update_fields=["next_outreach_at"])
        res, fake = self._post_send({"thread_id": "123", "text": "سلام"})
        self.assertEqual(res.status_code, 429)
        fake.direct_send.assert_not_called()

        self.account.next_outreach_at = None
        self.account.status = "COOLDOWN"
        self.account.save(update_fields=["next_outreach_at", "status"])
        res, fake = self._post_send({"thread_id": "123", "text": "سلام"})
        self.assertEqual(res.status_code, 429)
        fake.direct_send.assert_not_called()

    def test_outside_operating_window_blocks_send(self):
        from unittest.mock import patch

        with patch("apps.discovery.anti_ban.in_operating_window", return_value=False):
            res, fake = self._post_send({"thread_id": "123", "text": "سلام"})
        self.assertEqual(res.status_code, 429)
        fake.direct_send.assert_not_called()

    def test_manual_dm_marks_the_lead_so_outreach_skips_it(self):
        from apps.discovery.models import InstagramEngineLead

        lead = InstagramEngineLead.objects.create(tenant_id=self.account.tenant_id, username="buyer",
                                                  ig_user_pk="4242", status="NEW")
        res, fake = self._post_send({"user_id": "4242", "text": "سلام"})
        self.assertEqual(res.status_code, 200, res.content)
        lead.refresh_from_db()
        self.assertEqual(lead.status, "DM_SENT")
        self.assertEqual(lead.sender_account, "sender_acc")
        self.assertEqual(lead.initial_dm_text, "سلام")

