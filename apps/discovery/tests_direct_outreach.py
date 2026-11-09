from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.businesses.models import Business
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead, Customer
from apps.discovery.outreach.messages import extract_iranian_phone_number
from apps.discovery.outreach.replies import get_lead_conversation_thread

User = get_user_model()


class DirectChatInfrastructureTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="chat_tester", email="chat@test.com", password="password123")
        self.business = Business.objects.create(name="کلینیک چت تستی", user=self.user)
        self.tenant_key = f"tenant_{self.business.id}"

        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_key,
            username="raha.test",
            role="SENDER",
            status="ACTIVE",
            session_json={"cookies": {"sessionid": "mock_session_123"}},
        )

        self.lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_key,
            ig_user_pk="12345678",
            username="buyer_test",
            full_name="خریدار تست",
            comment_text="تعرفه اقساط کامپوزیت چقدر است؟",
            status="NEW",
            intent_score=0.95,
        )

    def test_extract_iranian_phone_number_formats(self):
        test_cases = [
            ("۰۹۱۰۲۲۰۷۸۵۵", "09102207855"),
            ("۰۹۱۲۹۴۱۰۵۷۸", "09129410578"),
            ("۰۹۰۳۲۰۹۸۱۶۰", "09032098160"),
            ("سلام شماره من 0912-345-6789 هست", "09123456789"),
            ("+989123456789", "09123456789"),
            ("00989123456789", "09123456789"),
            ("9123456789", "09123456789"),
            ("شماره من: ۰۹۱۲ ۳۴۵ ۶۷۸۹", "09123456789"),
            ("سلام قیمت چنده؟", None),
            ("", None),
            (None, None),
        ]
        for text, expected in test_cases:
            with self.subTest(text=text):
                self.assertEqual(extract_iranian_phone_number(text), expected)

    def test_get_lead_conversation_thread_legacy_reconstruction(self):
        lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_key,
            ig_user_pk="87654321",
            username="legacy_lead",
            initial_dm_text="سلام وقت بخیر مشاور کلینیک هستم",
            initial_dm_at=timezone.now(),
            sender_account="raha.test",
            has_replied=True,
            reply_text="سلام تعرفه ایمپلنت سوئیسی چنده؟",
            reply_at=timezone.now(),
            second_message_text="ایمپلنت سوئیسی با شرایط اقساطی ۱۰ ماهه ارائه میشه",
            second_sender_username="Pania.test",
            second_message_at=timezone.now(),
            conversation_history=[],
        )

        thread = get_lead_conversation_thread(lead)
        self.assertEqual(len(thread), 3)

        self.assertFalse(thread[0]["is_incoming"])
        self.assertEqual(thread[0]["sender"], "raha.test")
        self.assertIn("مشاور کلینیک", thread[0]["text"])

        self.assertTrue(thread[1]["is_incoming"])
        self.assertEqual(thread[1]["sender"], "legacy_lead")
        self.assertIn("تعرفه ایمپلنت", thread[1]["text"])

        self.assertFalse(thread[2]["is_incoming"])
        self.assertEqual(thread[2]["sender"], "Pania.test")
        self.assertIn("شرایط اقساطی", thread[2]["text"])

    def test_get_lead_conversation_thread_with_rich_history(self):
        self.lead.initial_dm_text = "سلام"
        self.lead.conversation_history = [
            {"sender": "buyer_test", "direction": "in", "text": "سلام شماره من ۰۹۱۲۳۴۵۶۷۸۹ هست", "time": "2026-11-04T12:00:00"},
            {"sender": "raha.test", "direction": "out", "text": "خیلی ممنون، تماس می‌گیریم", "time": "2026-11-04T12:05:00"},
        ]
        self.lead.save()

        thread = get_lead_conversation_thread(self.lead)
        self.assertEqual(len(thread), 3)
        self.assertEqual(thread[0]["direction"], "out")
        self.assertEqual(thread[1]["direction"], "in")
        self.assertEqual(thread[2]["direction"], "out")

    def test_update_lead_phone_view(self):
        client = Client()
        client.force_login(self.user)

        resp = client.post("/accounts/outreach/update-phone/", {
            "lead_id": self.lead.id,
            "phone_number": "۰۹۱۲۳۴۵۶۷۸۹",
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["phone_number"], "09123456789")

        # Verify Customer record was created/updated with normalized phone
        cust = Customer.objects.get(business=self.business, external_user_id="12345678")
        self.assertEqual(cust.phone_number, "09123456789")
