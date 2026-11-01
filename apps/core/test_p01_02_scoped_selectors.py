from unittest import mock
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from apps.businesses.models import Business
from apps.core.access_policy import PLATFORM_TENANT_ID
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
from apps.discovery.outreach_service import send_second_followup_message
from apps.products.models import Category
from apps.products.services import suggest_category_for_product

User = get_user_model()


class ScopedSelectorsTestCase(TestCase):
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

        # User without business
        self.user_no_biz = User.objects.create_user(username="nobiz@example.com", password="password123")

        # Senders
        self.sender_a = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_a,
            username="sender_a",
            role="SENDER",
            status="ACTIVE",
            session_json={"cookies": {"sessionid": "sess_a"}},
        )
        self.sender_b = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_b,
            username="sender_b",
            role="SENDER",
            status="ACTIVE",
            session_json={"cookies": {"sessionid": "sess_b"}},
        )
        self.sender_platform = InstagramEngineAccount.objects.create(
            tenant_id=PLATFORM_TENANT_ID,
            username="sender_platform",
            role="SENDER",
            status="ACTIVE",
            session_json={"cookies": {"sessionid": "sess_platform"}},
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

    def test_direct_service_call_without_actor_fails_closed(self):
        """Direct call to send_second_followup_message with no actor/tenant fails closed."""
        res = send_second_followup_message(
            lead_id=self.lead_a.id,
            sender_username="sender_a",
            message_text="Hello",
            actor_user_or_tenant=None,
        )
        self.assertFalse(res["success"])
        self.assertIn("fail-closed", res["error"])

    def test_user_without_business_fails_closed(self):
        """Call with an actor that has no business fails closed."""
        res = send_second_followup_message(
            lead_id=self.lead_a.id,
            sender_username="sender_a",
            message_text="Hello",
            actor_user_or_tenant=self.user_no_biz,
        )
        self.assertFalse(res["success"])
        self.assertIn("غیرمجاز", res["error"])

    @mock.patch("instagrapi.Client.direct_send")
    @mock.patch("instagrapi.Client.login_by_sessionid")
    def test_combo_1_own_lead_and_own_sender_allowed(self, mock_login, mock_send):
        """Combo 1: Tenant A sending to Lead A using Dedicated Sender A is allowed."""
        mock_send.return_value = mock.MagicMock(id="msg_1")
        res = send_second_followup_message(
            lead_id=self.lead_a.id,
            sender_username="sender_a",
            message_text="Hello from A",
            actor_user_or_tenant=self.user_a,
        )
        self.assertTrue(res["success"])

    @mock.patch("instagrapi.Client.direct_send")
    @mock.patch("instagrapi.Client.login_by_sessionid")
    def test_combo_2_own_lead_and_platform_sender_allowed(self, mock_login, mock_send):
        """Combo 2: Tenant A sending to Lead A using Shared Platform Sender is allowed."""
        mock_send.return_value = mock.MagicMock(id="msg_2")
        res = send_second_followup_message(
            lead_id=self.lead_a.id,
            sender_username="sender_platform",
            message_text="Hello from platform sender",
            actor_user_or_tenant=self.user_a,
        )
        self.assertTrue(res["success"])

    def test_combo_3_foreign_lead_rejected(self):
        """Combo 3: Tenant A attempting to message Lead B is rejected fail-closed."""
        res = send_second_followup_message(
            lead_id=self.lead_b.id,
            sender_username="sender_a",
            message_text="Cross-tenant intrusion attempt",
            actor_user_or_tenant=self.user_a,
        )
        self.assertFalse(res["success"])
        self.assertIn("غیرمجاز", res["error"])

    def test_combo_4_foreign_sender_rejected(self):
        """Combo 4: Tenant A attempting to use Sender B (dedicated to Tenant B) is rejected."""
        res = send_second_followup_message(
            lead_id=self.lead_a.id,
            sender_username="sender_b",
            message_text="Hijack sender attempt",
            actor_user_or_tenant=self.user_a,
        )
        self.assertFalse(res["success"])
        self.assertIn("متعلق به این کسب‌وکار یا استخر پلتفرم نیست", res["error"])

    def test_finding_r13_category_fallback_does_not_leak_foreign_categories(self):
        """Category suggestion fallback strictly excludes private categories of other businesses."""
        # Create private category for Business B
        Category.objects.create(
            business=self.business_b,
            name="دسته محرمانه و اختصاصی بیزینس ب",
            slug="private-b-cat",
            is_active=True,
        )

        # When Business A searches with no own or system categories matching, fallback must NOT return B's category
        suggestion = suggest_category_for_product(
            name="دسته محرمانه بیزینس",
            business=self.business_a,
        )
        if suggestion:
            self.assertNotEqual(suggestion.business_id, self.business_b.id)

    def test_finding_r13_api_category_attributes_enforces_tenant_boundary(self):
        """api_category_attributes prevents Tenant A from inspecting Business B's private category attributes."""
        private_cat_b = Category.objects.create(
            business=self.business_b,
            name="دسته لوکس ب",
            slug="luxury-b",
            suggested_attributes=["جنس نخ طلا", "فرمول اختصاصی ب"],
            is_active=True,
        )

        # Anonymous caller -> 401
        res = self.client.get(reverse("products:api_category_attributes", args=[private_cat_b.id]))
        self.assertEqual(res.status_code, 401)

        # Tenant A caller -> 404 (does not see B's category)
        self.client.force_login(self.user_a)
        res = self.client.get(reverse("products:api_category_attributes", args=[private_cat_b.id]))
        self.assertEqual(res.status_code, 404)

        # Tenant B caller -> 200 with attributes
        self.client.force_login(self.user_b)
        res = self.client.get(reverse("products:api_category_attributes", args=[private_cat_b.id]))
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["attributes"], ["جنس نخ طلا", "فرمول اختصاصی ب"])
