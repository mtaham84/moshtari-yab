"""
Tests for authentication, rate limiting, and tenant scoping on the product
category suggestion and category attribute endpoints (Roadmap 7.1 / D-1).
"""
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.db.models import Q
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from apps.businesses.models import Business
from . import views
from .models import Category

User = get_user_model()


class ProductCategoryApiGuardUnitTests(SimpleTestCase):
    """Fast unit tests (no DB required) for auth, HTTP method, and rate-limit guards."""

    def setUp(self):
        self.rf = RequestFactory()
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _authed_user(self, pk=101):
        return SimpleNamespace(pk=pk, is_authenticated=True, business=None)

    def test_anonymous_suggest_category_redirects_without_calling_llm(self):
        req = self.rf.get(reverse("products:api_suggest_category"), {"name": "دوره پایتون"})
        req.user = AnonymousUser()
        with patch("apps.products.views.suggest_category_for_product") as mock_fn:
            resp = views.api_suggest_category(req)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])
        mock_fn.assert_not_called()

    def test_anonymous_category_attributes_redirects_without_querying_db(self):
        req = self.rf.get(reverse("products:api_category_attributes", args=[1]))
        req.user = AnonymousUser()
        with patch("apps.products.views.Category.objects") as mock_qs:
            resp = views.api_category_attributes(req, category_id=1)
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])
        mock_qs.filter.assert_not_called()

    def test_suggest_category_rejects_post(self):
        req = self.rf.post(reverse("products:api_suggest_category"), {"name": "دوره پایتون"})
        req.user = self._authed_user()
        resp = views.api_suggest_category(req)
        self.assertEqual(resp.status_code, 405)

    def test_category_attributes_rejects_post(self):
        req = self.rf.post(reverse("products:api_category_attributes", args=[1]))
        req.user = self._authed_user()
        resp = views.api_category_attributes(req, category_id=1)
        self.assertEqual(resp.status_code, 405)

    def test_suggest_category_enforces_per_user_rate_limit(self):
        url = reverse("products:api_suggest_category")
        with patch("apps.products.views.SUGGEST_CATEGORY_HOURLY_LIMIT", 2), \
             patch("apps.products.views.suggest_category_for_product", return_value=None) as mock_fn:
            for _ in range(2):
                req = self.rf.get(url, {"name": "کفش ورزشی"})
                req.user = self._authed_user(pk=501)
                self.assertEqual(views.api_suggest_category(req).status_code, 200)

            blocked_req = self.rf.get(url, {"name": "کفش ورزشی"})
            blocked_req.user = self._authed_user(pk=501)
            blocked = views.api_suggest_category(blocked_req)
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(json.loads(blocked.content)["status"], "rate_limited")
            self.assertEqual(mock_fn.call_count, 2)

            # A different user keeps their own quota.
            other_req = self.rf.get(url, {"name": "کفش ورزشی"})
            other_req.user = self._authed_user(pk=502)
            self.assertEqual(views.api_suggest_category(other_req).status_code, 200)

    def test_category_attributes_enforces_per_user_rate_limit(self):
        url = reverse("products:api_category_attributes", args=[1])
        with patch("apps.products.views.CATEGORY_ATTRIBUTES_HOURLY_LIMIT", 1), \
             patch("apps.products.views.Category.objects") as mock_qs:
            mock_qs.filter.return_value.first.return_value = None
            req1 = self.rf.get(url)
            req1.user = self._authed_user(pk=601)
            views.api_category_attributes(req1, category_id=1)

            req2 = self.rf.get(url)
            req2.user = self._authed_user(pk=601)
            blocked = views.api_category_attributes(req2, category_id=1)
            self.assertEqual(blocked.status_code, 429)
            self.assertEqual(json.loads(blocked.content)["attributes"], [])
            self.assertEqual(mock_qs.filter.call_count, 1)

    def test_category_attributes_scopes_to_global_and_own_business(self):
        url = reverse("products:api_category_attributes", args=[42])
        biz = Business(id=77)
        req = self.rf.get(url)
        req.user = SimpleNamespace(pk=701, is_authenticated=True, business=biz)
        with patch("apps.products.views.Category.objects") as mock_qs:
            mock_qs.filter.return_value.first.return_value = None
            resp = views.api_category_attributes(req, category_id=42)
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(json.loads(resp.content), {"attributes": []})
        args, kwargs = mock_qs.filter.call_args
        self.assertEqual(args, (Q(business__isnull=True) | Q(business=biz),))
        self.assertEqual(kwargs, {"id": 42, "is_active": True})

        req_no_biz = self.rf.get(url)
        req_no_biz.user = SimpleNamespace(pk=702, is_authenticated=True, business=None)
        with patch("apps.products.views.Category.objects") as mock_qs:
            mock_qs.filter.return_value.first.return_value = None
            resp_no_biz = views.api_category_attributes(req_no_biz, category_id=42)
        self.assertEqual(resp_no_biz.status_code, 404)
        args, kwargs = mock_qs.filter.call_args
        self.assertEqual(args, (Q(business__isnull=True),))
        self.assertEqual(kwargs, {"id": 42, "is_active": True})


class ProductCategoryApiIntegrationTests(TestCase):
    """Full-stack DB tests covering URL routing, auth redirects, and seller category isolation."""

    def setUp(self):
        cache.clear()
        self.client = Client()
        self.suggest_url = reverse("products:api_suggest_category")
        self.seller_a = User.objects.create_user(username="seller_a@test.ir", password="pass")
        self.biz_a = Business.objects.create(user=self.seller_a, name="فروشگاه الف", onboarding_done=True)
        self.seller_b = User.objects.create_user(username="seller_b@test.ir", password="pass")
        self.biz_b = Business.objects.create(user=self.seller_b, name="فروشگاه ب", onboarding_done=True)

        self.global_cat = Category.objects.create(
            name="آموزش عمومی",
            slug="edu-auth-global",
            product_type="SERVICE",
            suggested_attributes=[{"key": "level", "label": "سطح"}],
        )
        self.private_cat_a = Category.objects.create(
            business=self.biz_a,
            name="دسته خصوصی الف",
            slug="private-a-auth-test",
            product_type="SERVICE",
            suggested_attributes=[{"key": "secret_attr", "label": "ویژگی خصوصی"}],
        )

    def tearDown(self):
        cache.clear()

    def test_anonymous_suggest_category_redirects_without_llm(self):
        with patch("apps.products.views.suggest_category_for_product") as mock_fn:
            resp = self.client.get(self.suggest_url, {"name": "دوره آموزشی"})
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])
        mock_fn.assert_not_called()

    def test_authenticated_suggest_category_works(self):
        self.client.force_login(self.seller_a)
        resp = self.client.get(self.suggest_url, {"name": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "empty")

    def test_anonymous_category_attributes_redirects(self):
        resp = self.client.get(reverse("products:api_category_attributes", args=[self.global_cat.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_seller_can_read_global_and_own_category_attributes_but_not_others(self):
        self.client.force_login(self.seller_b)
        global_resp = self.client.get(reverse("products:api_category_attributes", args=[self.global_cat.pk]))
        self.assertEqual(global_resp.status_code, 200)
        self.assertEqual(global_resp.json()["attributes"][0]["key"], "level")

        other_resp = self.client.get(reverse("products:api_category_attributes", args=[self.private_cat_a.pk]))
        self.assertEqual(other_resp.status_code, 404)
        self.assertEqual(other_resp.json()["attributes"], [])

        self.client.force_login(self.seller_a)
        own_resp = self.client.get(reverse("products:api_category_attributes", args=[self.private_cat_a.pk]))
        self.assertEqual(own_resp.status_code, 200)
        self.assertEqual(own_resp.json()["attributes"][0]["key"], "secret_attr")
