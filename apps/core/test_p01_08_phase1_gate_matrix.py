import io
from unittest import mock
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from apps.businesses.models import Business
from apps.core.access_policy import PLATFORM_TENANT_ID
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead

User = get_user_model()


class Phase1GateMatrixTestCase(TestCase):
    """
    P01.08 / Gate G01 Test Suite:
    Comprehensive verification of the full tenant isolation authorization matrix:
    1. Positive platform sender usage works smoothly across endpoints and CLI jobs.
    2. Foreign lead and foreign sender attempts are rejected strictly before network IO or DB mutation.
    3. No authorized request is rejected simply because the sender is a platform account.
    """

    def setUp(self):
        self.client = Client()

        # Tenant A
        self.user_a = User.objects.create_user(username="seller_a@example.com", password="password123")
        self.business_a = Business.objects.create(user=self.user_a, name="کسب‌وکار الف", onboarding_done=True)
        self.tenant_a = f"tenant_{self.business_a.id}"

        # Tenant B
        self.user_b = User.objects.create_user(username="seller_b@example.com", password="password123")
        self.business_b = Business.objects.create(user=self.user_b, name="کسب‌وکار ب", onboarding_done=True)
        self.tenant_b = f"tenant_{self.business_b.id}"

        # Senders
        self.sender_a = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_a,
            username="sender_a",
            role="SENDER",
            status="ACTIVE",
            daily_limit=20,
            hourly_limit=5,
            daily_action_count=0,
            session_json={"cookies": {"sessionid": "sess_a"}},
        )
        self.sender_b = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_b,
            username="sender_b",
            role="SENDER",
            status="ACTIVE",
            daily_limit=20,
            hourly_limit=5,
            daily_action_count=0,
            session_json={"cookies": {"sessionid": "sess_b"}},
        )
        self.sender_platform = InstagramEngineAccount.objects.create(
            tenant_id=PLATFORM_TENANT_ID,
            username="sender_platform",
            role="SENDER",
            status="ACTIVE",
            daily_limit=50,
            hourly_limit=10,
            daily_action_count=0,
            session_json={"cookies": {"sessionid": "sess_platform"}},
        )

        # Leads
        self.lead_a = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_a,
            username="lead_a",
            status="DM_SENT",
            ig_user_pk="11111",
            intent_score=0.9,
        )
        self.lead_b = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_b,
            username="lead_b",
            status="DM_SENT",
            ig_user_pk="22222",
            intent_score=0.9,
        )

        self.circadian_patcher = mock.patch("apps.discovery.sender_policy.SenderPolicy.is_within_circadian_hours", return_value=True)
        self.circadian_patcher.start()

    def tearDown(self):
        self.circadian_patcher.stop()

    @mock.patch("instagrapi.Client")
    def test_matrix_positive_dedicated_sender(self, mock_client_cls):
        """Matrix Cell 1: (Tenant A, Lead A, Sender A) -> 200 ALLOWED"""
        mock_instance = mock_client_cls.return_value
        mock_instance.login_by_sessionid.return_value = True
        mock_instance.direct_send.return_value = True

        self.client.force_login(self.user_a)
        url = reverse("accounts:send_followup")
        res = self.client.post(
            url,
            {"lead_id": self.lead_a.id, "sender_username": "sender_a", "message_text": "پیام معتبر"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))

    @mock.patch("instagrapi.Client")
    def test_matrix_positive_platform_sender(self, mock_client_cls):
        """Matrix Cell 2: (Tenant A, Lead A, Platform Sender) -> 200 ALLOWED (Product Directive #4)"""
        mock_instance = mock_client_cls.return_value
        mock_instance.login_by_sessionid.return_value = True
        mock_instance.direct_send.return_value = True

        self.client.force_login(self.user_a)
        url = reverse("accounts:send_followup")
        res = self.client.post(
            url,
            {"lead_id": self.lead_a.id, "sender_username": "sender_platform", "message_text": "پیام از اکانت اشتراکی"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json().get("success"))

    @mock.patch("instagrapi.Client")
    def test_matrix_negative_foreign_lead_dedicated_sender(self, mock_client_cls):
        """Matrix Cell 3: (Tenant A, Lead B, Sender A) -> 403 FORBIDDEN before IO/Client"""
        self.client.force_login(self.user_a)
        url = reverse("accounts:send_followup")
        res = self.client.post(
            url,
            {"lead_id": self.lead_b.id, "sender_username": "sender_a", "message_text": "تلاش غیرمجاز"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 403)
        mock_client_cls.assert_not_called()

    @mock.patch("instagrapi.Client")
    def test_matrix_negative_foreign_lead_platform_sender(self, mock_client_cls):
        """Matrix Cell 4: (Tenant A, Lead B, Platform Sender) -> 403 FORBIDDEN before IO/Client"""
        self.client.force_login(self.user_a)
        url = reverse("accounts:send_followup")
        res = self.client.post(
            url,
            {"lead_id": self.lead_b.id, "sender_username": "sender_platform", "message_text": "تلاش غیرمجاز با فرستنده پلتفرم"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 403)
        mock_client_cls.assert_not_called()

    @mock.patch("instagrapi.Client")
    def test_matrix_negative_own_lead_foreign_sender(self, mock_client_cls):
        """Matrix Cell 5: (Tenant A, Lead A, Sender B) -> 403 FORBIDDEN before IO/Client"""
        self.client.force_login(self.user_a)
        url = reverse("accounts:send_followup")
        res = self.client.post(
            url,
            {"lead_id": self.lead_a.id, "sender_username": "sender_b", "message_text": "تلاش برای دزدی فرستنده دیگری"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 403)
        mock_client_cls.assert_not_called()

    def test_cli_job_strictly_scopes_leads_to_target_tenant(self):
        """
        CLI command 'run_instagram_outreach --tenant tenant_a --once --dry-run'
        processes only Tenant A's leads and never touches Tenant B's leads.
        """
        # Create uncontacted lead for Tenant A and Tenant B
        lead_new_a = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_a,
            username="new_lead_a",
            status="NEW",
            intent_score=0.95,
            comment_text="نیاز به محصول الف دارم",
        )
        lead_new_b = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_b,
            username="new_lead_b",
            status="NEW",
            intent_score=0.95,
            comment_text="نیاز به محصول ب دارم",
        )

        out = io.StringIO()
        call_command(
            "run_instagram_outreach",
            tenant=self.tenant_a,
            once=True,
            dry_run=True,
            ignore_circadian=True,
            stdout=out,
        )
        output_text = out.getvalue()

        # Output must contain lead A and NOT lead B
        self.assertIn("@new_lead_a", output_text)
        self.assertNotIn("@new_lead_b", output_text)
