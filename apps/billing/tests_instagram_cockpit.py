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
