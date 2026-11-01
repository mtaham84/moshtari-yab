from datetime import timedelta
from unittest import mock
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from apps.businesses.models import Business
from apps.core.access_policy import PLATFORM_TENANT_ID
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead

User = get_user_model()


class FollowupAndEndpointsPolicyTestCase(TestCase):
    """
    P01.03 Test Suite:
    Validates that follow-up outreach and account management endpoints strictly
    enforce AccessPolicy boundaries:
    - Unauthorized requests fail-closed without instantiating Client or mutating counters/cooldowns.
    - Authorized requests using platform senders function properly and leak no secrets.
    """

    def setUp(self):
        self.client = Client()

        # Tenant A
        self.user_a = User.objects.create_user(username="user_a@example.com", password="password123")
        self.business_a = Business.objects.create(user=self.user_a, name="کسب‌وکار الف", onboarding_done=True)
        self.tenant_a = f"tenant_{self.business_a.id}"

        # Tenant B
        self.user_b = User.objects.create_user(username="user_b@example.com", password="password123")
        self.business_b = Business.objects.create(user=self.user_b, name="کسب‌وکار ب", onboarding_done=True)
        self.tenant_b = f"tenant_{self.business_b.id}"

        # Superuser (Platform Admin)
        self.admin_user = User.objects.create_superuser(username="admin@example.com", password="password123")

        # User without business
        self.user_no_biz = User.objects.create_user(username="nobiz@example.com", password="password123")

        # Sender Accounts
        self.sender_a = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_a,
            username="sender_a",
            role="SENDER",
            status="ACTIVE",
            daily_action_count=0,
            session_json={"cookies": {"sessionid": "sess_secret_a"}},
        )
        self.sender_b = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_b,
            username="sender_b",
            role="SENDER",
            status="COOLDOWN",
            cooldown_until=timezone.now() + timedelta(hours=2),
            daily_action_count=0,
            session_json={"cookies": {"sessionid": "sess_secret_b"}},
        )
        self.sender_platform = InstagramEngineAccount.objects.create(
            tenant_id=PLATFORM_TENANT_ID,
            username="sender_platform",
            role="SENDER",
            status="ACTIVE",
            daily_action_count=0,
            session_json={"cookies": {"sessionid": "sess_secret_platform"}},
        )

        # Leads
        self.lead_a = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_a,
            username="lead_customer_a",
            status="DM_SENT",
            ig_user_pk="12345",
        )
        self.lead_b = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_b,
            username="lead_customer_b",
            status="DM_SENT",
            ig_user_pk="67890",
        )

    @mock.patch("instagrapi.Client")
    def test_unauthorized_lead_access_does_not_instantiate_client_or_mutate_state(self, mock_client_cls):
        """
        Tenant A attempts to send follow-up to Tenant B's lead.
        Request MUST be rejected with 403 without instantiating Client or mutating DB.
        """
        self.client.force_login(self.user_a)
        url = reverse("accounts:send_followup")
        res = self.client.post(
            url,
            {
                "lead_id": self.lead_b.id,
                "sender_username": "sender_a",
                "message_text": "پیام نامجاز به لید ب",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 403)
        mock_client_cls.assert_not_called()

        self.lead_b.refresh_from_db()
        self.assertEqual(self.lead_b.status, "DM_SENT")
        self.assertFalse(self.lead_b.second_message_text)

        self.sender_a.refresh_from_db()
        self.assertEqual(self.sender_a.daily_action_count, 0)

    @mock.patch("instagrapi.Client")
    def test_unauthorized_sender_access_does_not_instantiate_client_or_mutate_state(self, mock_client_cls):
        """
        Tenant A attempts to send follow-up using Tenant B's private sender.
        Request MUST be rejected without instantiating Client or mutating DB.
        """
        self.client.force_login(self.user_a)
        url = reverse("accounts:send_followup")
        res = self.client.post(
            url,
            {
                "lead_id": self.lead_a.id,
                "sender_username": "sender_b",
                "message_text": "تلاش برای استفاده از فرستنده دیگری",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 403)
        mock_client_cls.assert_not_called()

        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.status, "DM_SENT")
        self.assertFalse(self.lead_a.second_message_text)

        self.sender_b.refresh_from_db()
        self.assertEqual(self.sender_b.daily_action_count, 0)

    def test_unauthorized_clear_cooldown_on_platform_sender_rejected(self):
        """
        Tenant A attempts to clear cooldown on platform sender account.
        Must be rejected with 403; cooldown and status MUST not change.
        """
        self.sender_platform.status = "COOLDOWN"
        future_cd = timezone.now() + timedelta(hours=3)
        self.sender_platform.cooldown_until = future_cd
        self.sender_platform.save()

        self.client.force_login(self.user_a)
        url = reverse("accounts:clear_outreach_cooldown", kwargs={"pk": self.sender_platform.pk})
        res = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(res.status_code, 403)

        self.sender_platform.refresh_from_db()
        self.assertEqual(self.sender_platform.status, "COOLDOWN")
        self.assertEqual(self.sender_platform.cooldown_until, future_cd)

    def test_unauthorized_clear_cooldown_on_foreign_tenant_sender_rejected(self):
        """
        Tenant A attempts to clear cooldown on Tenant B's private sender account.
        Must be rejected with 403; cooldown MUST not change.
        """
        self.client.force_login(self.user_a)
        url = reverse("accounts:clear_outreach_cooldown", kwargs={"pk": self.sender_b.pk})
        res = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(res.status_code, 403)

        self.sender_b.refresh_from_db()
        self.assertEqual(self.sender_b.status, "COOLDOWN")
        self.assertIsNotNone(self.sender_b.cooldown_until)

    def test_platform_admin_can_clear_cooldown_on_platform_sender(self):
        """
        Platform admin (superuser) CAN clear cooldown on platform account.
        """
        self.sender_platform.status = "COOLDOWN"
        self.sender_platform.cooldown_until = timezone.now() + timedelta(hours=1)
        self.sender_platform.save()

        self.client.force_login(self.admin_user)
        url = reverse("accounts:clear_outreach_cooldown", kwargs={"pk": self.sender_platform.pk})
        res = self.client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(res.status_code, 200)

        self.sender_platform.refresh_from_db()
        self.assertEqual(self.sender_platform.status, "ACTIVE")
        self.assertIsNone(self.sender_platform.cooldown_until)

    @mock.patch("instagrapi.Client")
    def test_authorized_platform_sender_followup_dispatches_and_leaks_no_secrets(self, mock_client_cls):
        """
        Tenant A dispatches follow-up using shared platform sender.
        Operation succeeds, updates lead and sender counter, and leaks no secret session cookies in response.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.login_by_sessionid.return_value = True
        mock_instance.direct_send.return_value = True

        self.client.force_login(self.user_a)
        url = reverse("accounts:send_followup")
        res = self.client.post(
            url,
            {
                "lead_id": self.lead_a.id,
                "sender_username": "sender_platform",
                "message_text": "سلام مجدد، جهت پیگیری سفارش خدمتتان پیام دادم.",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))

        # Verify no secrets or cookies leak in the response JSON
        payload_str = str(data)
        self.assertNotIn("sess_secret_platform", payload_str)
        self.assertNotIn("sessionid", payload_str)
        self.assertNotIn("cookies", payload_str)
        self.assertNotIn("password", payload_str)

        # Verify Client was logged in with the session cookie and sent direct
        mock_instance.login_by_sessionid.assert_called_once_with("sess_secret_platform")
        mock_instance.direct_send.assert_called_once_with(
            "سلام مجدد، جهت پیگیری سفارش خدمتتان پیام دادم.",
            user_ids=[12345],
        )

        # Verify state mutation occurred only on authorized records
        self.lead_a.refresh_from_db()
        self.assertEqual(self.lead_a.status, "FOLLOWUP_SENT")
        self.assertEqual(self.lead_a.second_sender_username, "sender_platform")
        self.assertEqual(self.lead_a.second_message_text, "سلام مجدد، جهت پیگیری سفارش خدمتتان پیام دادم.")

        self.sender_platform.refresh_from_db()
        self.assertEqual(self.sender_platform.daily_action_count, 1)

    def test_fail_closed_unauthenticated_or_no_business(self):
        """
        Unauthenticated user or user without business cannot access send_followup.
        """
        url = reverse("accounts:send_followup")
        res = self.client.post(url, {"lead_id": self.lead_a.id, "sender_username": "sender_a"})
        self.assertEqual(res.status_code, 302)  # Redirects to login

        self.client.force_login(self.user_no_biz)
        res = self.client.post(url, {"lead_id": self.lead_a.id, "sender_username": "sender_a"})
        self.assertEqual(res.status_code, 302)  # Redirects to dashboard
