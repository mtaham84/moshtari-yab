from unittest import mock
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from apps.businesses.models import Business
from apps.core.access_policy import PLATFORM_TENANT_ID
from apps.discovery.models import InstagramEngineAccount

User = get_user_model()


class SecureAccountConnectingTestCase(TestCase):
    """
    P01.04 Test Suite:
    Validates that the existing account connection & editing endpoints:
    - Never allow foreign or platform accounts to be hijacked or overwritten (R02).
    - Enforce identity verification between session and claimed username.
    - Never save unverified sessions or fallback cookies on failed authentication.
    - Strictly preserve platform ownership.
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

        # Existing Healthy Accounts
        self.sender_a = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_a,
            username="sender_a",
            role="SENDER",
            status="ACTIVE",
            daily_limit=20,
            session_json={"cookies": {"sessionid": "healthy_sess_a", "ds_user_id": "111"}},
        )
        self.sender_platform = InstagramEngineAccount.objects.create(
            tenant_id=PLATFORM_TENANT_ID,
            username="sender_platform",
            role="SENDER",
            status="ACTIVE",
            daily_limit=50,
            session_json={"cookies": {"sessionid": "healthy_sess_platform", "ds_user_id": "999"}},
        )

    def test_foreign_account_input_cannot_overwrite_healthy_record(self):
        """
        Tenant B attempts to add Tenant A's account ('sender_a').
        Must be rejected with 403; Tenant A's account MUST not be modified.
        """
        self.client.force_login(self.user_b)
        url = reverse("accounts:add_outreach_account")
        res = self.client.post(
            url,
            {
                "username": "sender_a",
                "sessionid": "attacker_cookie_123456789012345678901234567890",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 403)

        self.sender_a.refresh_from_db()
        self.assertEqual(self.sender_a.tenant_id, self.tenant_a)
        self.assertEqual(self.sender_a.session_json.get("cookies", {}).get("sessionid"), "healthy_sess_a")

    def test_platform_sender_cannot_be_hijacked_by_tenant(self):
        """
        Tenant A attempts to add/hijack platform account ('sender_platform').
        Must be rejected with 403; platform ownership and session MUST be preserved.
        """
        self.client.force_login(self.user_a)
        url = reverse("accounts:add_outreach_account")
        res = self.client.post(
            url,
            {
                "username": "sender_platform",
                "sessionid": "hijack_cookie_123456789012345678901234567890",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 403)

        self.sender_platform.refresh_from_db()
        self.assertEqual(self.sender_platform.tenant_id, PLATFORM_TENANT_ID)
        self.assertEqual(self.sender_platform.session_json.get("cookies", {}).get("sessionid"), "healthy_sess_platform")

    @mock.patch("instagrapi.Client")
    def test_session_identity_mismatch_rejected_without_overwriting_record(self, mock_client_cls):
        """
        Tenant A attempts to update 'sender_a' using a session belonging to '@attacker_ig'.
        The client detects mismatch and rejects with 400; 'sender_a' session is untouched.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.login_by_sessionid.return_value = True
        mock_instance.username = "attacker_ig"
        mock_instance.get_settings.return_value = {"settings": "attacker_data"}

        self.client.force_login(self.user_a)
        url = reverse("accounts:add_outreach_account")
        res = self.client.post(
            url,
            {
                "username": "sender_a",
                "sessionid": "mismatched_cookie_123456789012345678901234567890",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("تطابق ندارد", res.json().get("error", ""))

        self.sender_a.refresh_from_db()
        self.assertEqual(self.sender_a.session_json.get("cookies", {}).get("sessionid"), "healthy_sess_a")

    @mock.patch("instagrapi.Client")
    def test_failed_login_does_not_save_unverified_account_or_cookie(self, mock_client_cls):
        """
        Tenant A attempts to add a new account 'brand_new_sender', but login fails (e.g. network/credentials).
        Must reject with 400 and NOT save any unverified record in the database.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.login_by_sessionid.side_effect = RuntimeError("Instagram login failed: checkpoint_required")

        self.client.force_login(self.user_a)
        url = reverse("accounts:add_outreach_account")
        res = self.client.post(
            url,
            {
                "username": "brand_new_sender",
                "sessionid": "invalid_cookie_123456789012345678901234567890",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 400)

        # Ensure no unverified account was persisted
        self.assertFalse(InstagramEngineAccount.objects.filter(username="brand_new_sender").exists())

    @mock.patch("instagrapi.Client")
    def test_successful_verified_account_connects_properly(self, mock_client_cls):
        """
        Tenant A connects a new verified account where session matches claimed username.
        Account is persisted with status ACTIVE and isolated tenant pitch.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.login_by_sessionid.return_value = True
        mock_instance.username = "verified_new_sender"
        mock_instance.get_settings.return_value = {"cookies": {"sessionid": "valid_session_123"}}

        self.client.force_login(self.user_a)
        url = reverse("accounts:add_outreach_account")
        res = self.client.post(
            url,
            {
                "username": "verified_new_sender",
                "sessionid": "valid_session_123456789012345678901234567890",
                "consultant_name": "سارا",
                "pitch_template": "سلام از طرف سارا",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))

        new_acc = InstagramEngineAccount.objects.filter(username="verified_new_sender").first()
        self.assertIsNotNone(new_acc)
        self.assertEqual(new_acc.tenant_id, self.tenant_a)
        self.assertEqual(new_acc.status, "ACTIVE")

    @mock.patch("instagrapi.Client")
    def test_edit_outreach_account_rejects_session_mismatch(self, mock_client_cls):
        """
        Tenant A edits existing account 'sender_a', but session belongs to '@diff_user'.
        Must reject with 400 and NOT overwrite 'sender_a' healthy session.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.login_by_sessionid.return_value = True
        mock_instance.username = "diff_user"

        self.client.force_login(self.user_a)
        url = reverse("accounts:edit_outreach_account", kwargs={"pk": self.sender_a.pk})
        res = self.client.post(
            url,
            {"sessionid": "diff_user_cookie_12345678901234567890"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 400)

        self.sender_a.refresh_from_db()
        self.assertEqual(self.sender_a.session_json.get("cookies", {}).get("sessionid"), "healthy_sess_a")

    @mock.patch("instagrapi.Client")
    def test_edit_outreach_account_rejects_failed_login_without_fallback_corruption(self, mock_client_cls):
        """
        Tenant A edits existing account 'sender_a', but login throws exception.
        Must reject with 400 and NOT overwrite 'sender_a' healthy session with fallback cookies.
        """
        mock_instance = mock_client_cls.return_value
        mock_instance.login_by_sessionid.side_effect = ConnectionError("Proxy connection failed")

        self.client.force_login(self.user_a)
        url = reverse("accounts:edit_outreach_account", kwargs={"pk": self.sender_a.pk})
        res = self.client.post(
            url,
            {"sessionid": "broken_cookie_12345678901234567890"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 400)

        self.sender_a.refresh_from_db()
        self.assertEqual(self.sender_a.session_json.get("cookies", {}).get("sessionid"), "healthy_sess_a")
