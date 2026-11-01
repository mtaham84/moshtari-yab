from unittest.mock import Mock
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory, TestCase
from django.urls import resolve, reverse

import apps.accounts.auth_views as auth_views
import apps.accounts.views as accounts_views

User = get_user_model()


class P0815AuthViewsModularizationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.factory = RequestFactory()

    def test_re_export_compatibility(self):
        """
        Verify that all auth views and helpers are imported and exported
        identically by apps.accounts.views and apps.accounts.auth_views.
        """
        symbols = [
            "get_client_ip",
            "check_auth_throttle",
            "increment_auth_throttle",
            "reset_auth_throttle",
            "is_valid_email",
            "signup_view",
            "login_view",
            "logout_view",
        ]
        for sym in symbols:
            self.assertTrue(
                hasattr(auth_views, sym),
                f"apps.accounts.auth_views missing {sym}",
            )
            self.assertTrue(
                hasattr(accounts_views, sym),
                f"apps.accounts.views backward compatibility missing {sym}",
            )
            self.assertIs(
                getattr(auth_views, sym),
                getattr(accounts_views, sym),
                f"{sym} in views is not the exact object from auth_views",
            )

    def test_url_reversal_and_resolution(self):
        """
        Ensure accounts URL patterns resolve to the extracted auth_views functions.
        """
        signup_resolver = resolve(reverse("accounts:signup"))
        self.assertEqual(signup_resolver.func, auth_views.signup_view)

        login_resolver = resolve(reverse("accounts:login"))
        self.assertEqual(login_resolver.func, auth_views.login_view)

        logout_resolver = resolve(reverse("accounts:logout"))
        self.assertEqual(logout_resolver.func, auth_views.logout_view)

    def test_get_client_ip_variants(self):
        """
        Test get_client_ip parses HTTP_X_FORWARDED_FOR or falls back to REMOTE_ADDR.
        """
        req_proxy = self.factory.get("/", HTTP_X_FORWARDED_FOR="203.0.113.195, 70.41.3.18")
        self.assertEqual(auth_views.get_client_ip(req_proxy), "203.0.113.195")

        req_direct = self.factory.get("/", REMOTE_ADDR="198.51.100.42")
        self.assertEqual(auth_views.get_client_ip(req_direct), "198.51.100.42")

        req_empty = Mock(META={})
        self.assertEqual(auth_views.get_client_ip(req_empty), "127.0.0.1")

    def test_throttle_helpers_lifecycle(self):
        """
        Test throttle check, increment, and reset cycles.
        """
        req = self.factory.get("/", REMOTE_ADDR="192.0.2.1")
        self.assertFalse(auth_views.check_auth_throttle(req, "test_prefix", max_attempts=3))

        c1 = auth_views.increment_auth_throttle(req, "test_prefix")
        self.assertEqual(c1, 1)
        self.assertFalse(auth_views.check_auth_throttle(req, "test_prefix", max_attempts=3))

        auth_views.increment_auth_throttle(req, "test_prefix")
        c3 = auth_views.increment_auth_throttle(req, "test_prefix")
        self.assertEqual(c3, 3)
        self.assertTrue(auth_views.check_auth_throttle(req, "test_prefix", max_attempts=3))

        auth_views.reset_auth_throttle(req, "test_prefix")
        self.assertFalse(auth_views.check_auth_throttle(req, "test_prefix", max_attempts=3))

    def test_is_valid_email(self):
        """
        Validate valid and invalid email addresses using is_valid_email helper.
        """
        self.assertTrue(auth_views.is_valid_email("user@example.com"))
        self.assertTrue(auth_views.is_valid_email("seller.store+tag@sub.domain.org"))
        self.assertFalse(auth_views.is_valid_email(""))
        self.assertFalse(auth_views.is_valid_email(None))
        self.assertFalse(auth_views.is_valid_email("invalid-email"))
        self.assertFalse(auth_views.is_valid_email("user@domain..com"))

    def test_logout_redirects_to_landing(self):
        """
        Logout view should invalidate session and redirect to landing page.
        """
        user = User.objects.create_user(
            username="logout_user@example.com",
            email="logout_user@example.com",
            password="Password123!",
        )
        self.client.force_login(user)
        self.assertEqual(int(self.client.session.get("_auth_user_id")), user.pk)

        res = self.client.get(reverse("accounts:logout"))
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, reverse("core:landing"))
        self.assertNotIn("_auth_user_id", self.client.session)
