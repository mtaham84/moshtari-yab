from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.businesses.models import Business
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
        with patch("apps.discovery.outreach_service.send_chat_message") as direct_send:
            r = self.client.post(self.url, {"lead_id": self.lead.id, "sender_username": "our_sender",
                                            "message_text": "سلام، در خدمتم"}, **self.ajax)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["queued"])
        direct_send.assert_not_called()
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
