from unittest import mock
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from apps.businesses.models import Business
from apps.products.models import Category
from apps.products.services import suggest_category_for_product

User = get_user_model()


class PaidSuggestionBoundaryTestCase(TestCase):
    """
    P02.01 Test Suite:
    Validates that the category suggestion endpoint and service:
    - Never invoke paid LLM on anonymous, GET, or invalid CSRF requests (R07).
    - Enforces input length limits on name and description.
    - Preserves queued contract response for paid AI requests pending Phase 6 financial wiring.
    - Prevents model responses from selecting or returning foreign tenant categories (R13 defense).
    """

    def setUp(self):
        self.client = Client()

        # Tenant A
        self.user_a = User.objects.create_user(username="seller_a@example.com", password="password123")
        self.business_a = Business.objects.create(user=self.user_a, name="فروشگاه الف", onboarding_done=True)

        # Tenant B
        self.user_b = User.objects.create_user(username="seller_b@example.com", password="password123")
        self.business_b = Business.objects.create(user=self.user_b, name="فروشگاه ب", onboarding_done=True)

        # User without business
        self.user_no_biz = User.objects.create_user(username="nobiz@example.com", password="password123")

        # Clear fixtures for precise boundary checks
        Category.objects.all().delete()

        # Business B's Private Category
        self.private_cat_b = Category.objects.create(
            name="شال مجلسی سفارشی ب",
            business=self.business_b,
            is_active=True,
        )

        # System Category
        self.system_cat = Category.objects.create(
            name="کیف اداری چرم",
            business=None,
            is_active=True,
        )

    @mock.patch("apps.core.llm.chat_json")
    def test_anonymous_caller_rejected_without_llm_invocation(self, mock_chat_json):
        """
        Anonymous GET and POST requests are rejected with 401 without invoking LLM.
        """
        url = reverse("products:api_suggest_category")

        res_get = self.client.get(url, {"name": "محصول تستی ناشناس"})
        self.assertEqual(res_get.status_code, 401)
        mock_chat_json.assert_not_called()

        res_post = self.client.post(url, {"name": "محصول تستی ناشناس"})
        self.assertEqual(res_post.status_code, 401)
        mock_chat_json.assert_not_called()

    @mock.patch("apps.core.llm.chat_json")
    def test_user_without_business_rejected_without_llm_invocation(self, mock_chat_json):
        """
        Authenticated user without an active business is rejected with 403 without invoking LLM.
        """
        self.client.force_login(self.user_no_biz)
        url = reverse("products:api_suggest_category")

        res = self.client.get(url, {"name": "محصول بدون کسب‌وکار"})
        self.assertEqual(res.status_code, 403)
        mock_chat_json.assert_not_called()

    @mock.patch("apps.core.llm.chat_json")
    def test_get_request_is_strictly_heuristic_and_never_invokes_paid_llm(self, mock_chat_json):
        """
        GET request for an obscure product with no heuristic match returns no_match
        and NEVER invokes paid LLM (chat_json).
        """
        self.client.force_login(self.user_a)
        url = reverse("products:api_suggest_category")

        # Obscure name with no heuristic match
        res = self.client.get(url, {"name": "قطعه صنعتی فوق‌پیشرفته توربین ایکس"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "no_match")
        mock_chat_json.assert_not_called()

    def test_invalid_csrf_post_is_blocked_by_django_security(self):
        """
        POST request with CSRF enforcement and invalid token is blocked with 403.
        """
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user_a)
        url = reverse("products:api_suggest_category")

        # Send POST without CSRF cookie/token
        res = csrf_client.post(url, {"name": "محصول تستی بدون توکن"})
        self.assertEqual(res.status_code, 403)

    def test_input_length_limits_enforced(self):
        """
        Inputs exceeding character boundaries are rejected with 400.
        """
        self.client.force_login(self.user_a)
        url = reverse("products:api_suggest_category")

        # Exceeding name length (> 255)
        long_name = "الف" * 256
        res = self.client.get(url, {"name": long_name})
        self.assertEqual(res.status_code, 400)
        self.assertIn("۲۵۵", res.json().get("message", ""))

        # Exceeding description length (> 2000)
        long_desc = "توضیح" * 500
        res = self.client.get(url, {"name": "کالای معتبر", "description": long_desc})
        self.assertEqual(res.status_code, 400)
        self.assertIn("۲۰۰۰", res.json().get("message", ""))

    @mock.patch("apps.core.llm.chat_json")
    def test_paid_ai_request_returns_queued_contract_without_direct_unmetered_llm(self, mock_chat_json):
        """
        POST request requesting AI returns queued contract response for Phase 6 financial wiring.
        """
        self.client.force_login(self.user_a)
        url = reverse("products:api_suggest_category")

        res = self.client.post(
            url,
            {"name": "محصول نیازمند هوش مصنوعی", "allow_ai": "true"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data.get("status"), "queued")
        self.assertIn("صف اعتبارسنجی مالی فاز ۶", data.get("message", ""))
        mock_chat_json.assert_not_called()

    @mock.patch("apps.core.llm.chat_json")
    def test_model_response_cannot_select_foreign_tenant_category(self, mock_chat_json):
        """
        R13 Defense: Even if LLM hallucinates or returns Business B's private category ID,
        suggest_category_for_product strictly discards it and returns None.
        """
        # Mock LLM to return Business B's private category ID
        mock_chat_json.return_value = {
            "category_id": self.private_cat_b.id,
            "reason": "تطابق با شال مجلسی سفارشی",
        }

        # Tenant A calls service with allow_ai=True
        suggestion = suggest_category_for_product(
            name="شال مجلسی",
            business=self.business_a,
            allow_ai=True,
        )

        # Must discard foreign category and return None
        self.assertIsNone(suggestion)
