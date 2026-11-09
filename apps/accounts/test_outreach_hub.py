from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.businesses.models import Business
from apps.discovery.instagram_scoring import update_lead_scoring
from apps.discovery.models import Customer, InstagramEngineAccount, InstagramEngineLead

User = get_user_model()


class OutreachHubSafeQueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="hub@example.com", password="secret-pass-1")
        self.business = Business.objects.create(user=self.user, name="کلینیک", business_domain="")
        self.tenant = f"tenant_{self.business.id}"
        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant, username="our_sender", password="x", role="SENDER", status="ACTIVE")
        self.lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant, username="buyer", ig_user_pk="555", status="REPLIED", has_replied=True,
            conversation_history=[{"direction": "in", "sender": "buyer", "text": "شمارم ۰۹۱۲۳۴۵۶۷۸۹ هست"}])
        other_user = User.objects.create_user(username="other@example.com", password="secret-pass-1")
        other_biz = Business.objects.create(user=other_user, name="دیگری", business_domain="")
        self.other_sender = InstagramEngineAccount.objects.create(
            tenant_id=f"tenant_{other_biz.id}", username="their_sender", password="x", role="SENDER", status="ACTIVE")
        self.other_lead = InstagramEngineLead.objects.create(
            tenant_id=f"tenant_{other_biz.id}", username="their_buyer", ig_user_pk="777", status="REPLIED")
        self.client.force_login(self.user)
        self.url = reverse("accounts:send_followup")
        self.ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    def test_manual_dm_goes_to_safe_queue_not_direct_send(self):
        r = self.client.post(self.url, {"lead_id": self.lead.id, "sender_username": "our_sender",
                                        "message_text": "سلام، در خدمتم"}, **self.ajax)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["queued"])
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "QUEUED")
        self.assertEqual(self.lead.conversation_history[-1]["delivery_status"], "QUEUED")

    def test_cannot_queue_for_other_tenants_lead_or_sender(self):
        r = self.client.post(self.url, {"lead_id": self.other_lead.id, "message_text": "x"}, **self.ajax)
        self.assertEqual(r.status_code, 404)
        r = self.client.post(self.url, {"lead_id": self.lead.id, "sender_username": "their_sender",
                                        "message_text": "x"}, **self.ajax)
        self.assertEqual(r.status_code, 403)
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "REPLIED")

    def test_chat_history_returns_captured_phone(self):
        r = self.client.get(reverse("accounts:lead_chat_history", args=[self.lead.id]), **self.ajax)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["lead"]["captured_phone"], "09123456789")

    def test_saved_customer_phone_wins_and_other_business_phone_is_ignored(self):
        other_biz = Business.objects.exclude(pk=self.business.pk).get()
        Customer.objects.create(business=other_biz, source_platform="instagram", external_user_id="555",
                                phone_number="09990000000")
        r = self.client.get(reverse("accounts:lead_chat_history", args=[self.lead.id]), **self.ajax)
        self.assertEqual(r.json()["lead"]["captured_phone"], "09123456789")
        Customer.objects.create(business=self.business, source_platform="instagram", external_user_id="555",
                                phone_number="09351112233")
        r = self.client.get(reverse("accounts:lead_chat_history", args=[self.lead.id]), **self.ajax)
        self.assertEqual(r.json()["lead"]["captured_phone"], "09351112233")

    def test_outreach_page_shows_queued_badge_and_phone(self):
        self.lead.status = "QUEUED"
        self.lead.save(update_fields=["status"])
        r = self.client.get(reverse("accounts:outreach"))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("⏳ در صف ارسال", html)
        self.assertIn("09123456789", html)

    def test_outreach_page_shows_full_45_point_breakdown(self):
        lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant, username="fresh_buyer", ig_user_pk="888", status="NEW", is_private=False,
            comment_text="قیمت کامپوزیت چنده؟", comment_created_at=timezone.now())
        update_lead_scoring(lead)
        html = self.client.get(reverse("accounts:outreach")).content.decode()
        self.assertIn("۴۵ امتیازی", html)
        self.assertIn("پیج پابلیک (+۱۵)", html)
        self.assertIn(f"خدمت: {lead.score_breakdown['service_name']} (+{lead.score_breakdown['service_score']})", html)


class OutreachExportTenantTests(TestCase):
    """BUG-05: the CSV export must use the same tenant keys (incl. legacy aliases) as the outreach page."""

    def setUp(self):
        self.user = User.objects.create_user(username="csv@example.com", password="secret-pass-1")
        self.business = Business.objects.create(user=self.user, name="کلینیک", business_domain="")
        InstagramEngineLead.objects.create(tenant_id="legacy_alias", username="old_buyer", ig_user_pk="1",
                                           comment_text="قیمت؟")
        InstagramEngineLead.objects.create(tenant_id=f"tenant_{self.business.id}", username="new_buyer",
                                           ig_user_pk="2", comment_text="قیمت؟")
        InstagramEngineLead.objects.create(tenant_id="tenant_999999", username="foreign_buyer", ig_user_pk="3",
                                           comment_text="قیمت؟")
        self.client.force_login(self.user)

    def test_export_includes_legacy_alias_leads(self):
        aliases = {"legacy_alias": f"tenant_{self.business.id}"}
        with patch.dict("apps.discovery.tenants.TENANT_ALIASES", aliases, clear=True):
            res = self.client.get(reverse("accounts:outreach_export"), {"five_days": "0"})
        body = res.content.decode("utf-8-sig")
        self.assertEqual(res.status_code, 200)
        self.assertIn("old_buyer", body)
        self.assertIn("new_buyer", body)
        self.assertNotIn("foreign_buyer", body)

    def test_tenant_keys_for_business(self):
        from apps.discovery.tenants import tenant_keys_for_business

        self.assertEqual(tenant_keys_for_business(4), ["tenant_4", "sarv"])
        self.assertEqual(tenant_keys_for_business(self.business), [f"tenant_{self.business.id}"])


class OutreachReadOnlyGetTests(TestCase):
    """BUG-09: opening the outreach page or its CSV export must not write to the database."""

    def setUp(self):
        self.user = User.objects.create_user(username="ro@example.com", password="secret-pass-1")
        self.business = Business.objects.create(user=self.user, name="کلینیک", business_domain="")
        InstagramEngineLead.objects.create(tenant_id=f"tenant_{self.business.id}", username="buyer", ig_user_pk="1",
                                           comment_text="قیمت؟", outreach_score=0, comment_created_at=timezone.now())
        self.client.force_login(self.user)

    def _writes(self, url, params=None):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            res = self.client.get(url, params or {})
        self.assertEqual(res.status_code, 200)
        return [q["sql"] for q in ctx.captured_queries if q["sql"].lstrip().upper().startswith(("UPDATE", "INSERT"))]

    def test_export_is_read_only(self):
        self.assertEqual(self._writes(reverse("accounts:outreach_export"), {"five_days": "0"}), [])

    def test_outreach_page_does_not_store_scores(self):
        writes = self._writes(reverse("accounts:outreach"))
        self.assertFalse([sql for sql in writes if "discovery_instagramengineleads" in sql.lower()
                          or "instagramenginelead" in sql.lower()], writes)
