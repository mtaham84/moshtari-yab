from decimal import Decimal
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse

from apps.billing.models import AIModel, Provider

User = get_user_model()


class P0819ModelPriorityComponentTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_superuser(
            username="ops_admin_p0819", email="ops19@example.com", password="pass"
        )
        self.seller_user = User.objects.create_user(
            username="seller_p0819", email="seller19@example.com", password="pass"
        )
        self.client = Client()

        self.provider = Provider.objects.create(
            name="TestLLMProvider",
            base_url="https://api.testllm.com/v1",
            api_key="sk-test-secret-12345",
        )
        self.model_a = AIModel.objects.create(
            provider=self.provider,
            name="gpt-model-a",
            label="Model Alpha",
            priority=10,
            use_extract=True,
            input_price_usd=Decimal("0.5"),
            output_price_usd=Decimal("1.5"),
        )
        self.model_b = AIModel.objects.create(
            provider=self.provider,
            name="gpt-model-b",
            label="Model Beta",
            priority=20,
            use_extract=True,
            input_price_usd=Decimal("0.2"),
            output_price_usd=Decimal("0.8"),
        )

    def test_model_priority_component_rendering_and_dom_safety(self):
        """
        Component must render model-priority table with clean data attributes,
        unobtrusive data-confirm (no inline onsubmit), and no raw template literal sinks.
        """
        html = render_to_string(
            "ops/components/_model_priority_table.html",
            {"p": self.provider},
        )
        # Class and structure checks
        self.assertIn("class=\"model-priority-table\"", html)
        self.assertIn("data-provider-id=\"{}\"".format(self.provider.pk), html)
        self.assertIn("data-model-id=\"{}\"".format(self.model_a.pk), html)
        self.assertIn("data-priority=\"10\"", html)

        # Priority form and CSRF presence
        self.assertIn("class=\"inline priority-form\"", html)
        self.assertIn("name=\"priority\" value=\"10\"", html)

        # Unobtrusive event delegation: must NOT have inline onsubmit handler
        self.assertNotIn("onsubmit=\"return confirm", html)
        self.assertIn("data-confirm=", html)

    def test_model_priority_update_endpoint_ajax_and_form(self):
        self.client.force_login(self.staff_user)
        url = reverse("ops:model_priority_update", args=[self.model_a.pk])

        # 1. AJAX JSON request
        resp_ajax = self.client.post(
            url,
            {"priority": "35"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp_ajax.status_code, 200)
        data = resp_ajax.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["priority"], 35)

        self.model_a.refresh_from_db()
        self.assertEqual(self.model_a.priority, 35)

        # 2. Standard form POST redirects to providers page
        resp_form = self.client.post(url, {"priority": "5"})
        self.assertEqual(resp_form.status_code, 302)
        self.assertEqual(resp_form.url, reverse("ops:providers"))
        self.model_a.refresh_from_db()
        self.assertEqual(self.model_a.priority, 5)

    def test_model_priority_update_bounds_and_validation(self):
        self.client.force_login(self.staff_user)
        url = reverse("ops:model_priority_update", args=[self.model_a.pk])

        # Non-numeric rejected
        resp_bad = self.client.post(
            url,
            {"priority": "invalid-str"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp_bad.status_code, 400)
        self.model_a.refresh_from_db()
        self.assertEqual(self.model_a.priority, 10)  # Unchanged

        # Exceeds max 1000 clamped or rejected
        resp_over = self.client.post(
            url,
            {"priority": "9999"},
            headers={"x-requested-with": "XMLHttpRequest"},
        )
        self.assertEqual(resp_over.status_code, 400)

    def test_model_priority_update_forbidden_for_non_staff(self):
        self.client.force_login(self.seller_user)
        url = reverse("ops:model_priority_update", args=[self.model_a.pk])
        resp = self.client.post(url, {"priority": "1"})
        self.assertEqual(resp.status_code, 403)
        self.model_a.refresh_from_db()
        self.assertEqual(self.model_a.priority, 10)
