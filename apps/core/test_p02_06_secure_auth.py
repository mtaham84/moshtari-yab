from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, Client
from django.urls import reverse

from apps.businesses.models import Business

User = get_user_model()


class SecureAuthMVPTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()

    def test_valid_signup_without_email_verification(self):
        """
        Valid signup logs the user in immediately, creates User and Business atomically,
        and does NOT require or trigger email verification (Directive 6).
        """
        post_data = {
            "first_name": "علی",
            "last_name": "محمدی",
            "email": "ali.mohammadi@example.com",
            "password": "ValidPassword123!",
            "business_name": "فروشگاه آرایشی علی",
            "business_type": "PHYSICAL",
            "business_domain": "آرایشی",
        }
        res = self.client.post(reverse("accounts:signup"), post_data)
        self.assertEqual(res.status_code, 302)
        self.assertEqual(res.url, reverse("accounts:onboarding"))

        # User must exist and be logged in immediately
        user = User.objects.filter(email="ali.mohammadi@example.com").first()
        self.assertIsNotNone(user)
        self.assertTrue(user.is_active)
        self.assertEqual(int(self.client.session.get("_auth_user_id")), user.pk)

        # Business must be created
        biz = Business.objects.filter(user=user).first()
        self.assertIsNotNone(biz)
        self.assertEqual(biz.name, "فروشگاه آرایشی علی")

    def test_password_with_leading_and_trailing_spaces_preserved(self):
        """
        Password must NOT be stripped. Leading/trailing spaces intentionally chosen
        by the user must be preserved (R28).
        """
        spaced_password = "   my spaced pass 999   "
        post_data = {
            "first_name": "سارا",
            "last_name": "احمدی",
            "email": "sara.spaced@example.com",
            "password": spaced_password,
            "business_name": "کتابفروشی سارا",
            "business_type": "DIGITAL",
            "business_domain": "کتاب",
        }
        res = self.client.post(reverse("accounts:signup"), post_data)
        self.assertEqual(res.status_code, 302)

        # Log out
        self.client.logout()

        # Login with stripped password should FAIL
        stripped_res = self.client.post(reverse("accounts:login"), {
            "username_or_email": "sara.spaced@example.com",
            "password": spaced_password.strip(),
        })
        self.assertContains(stripped_res, "نام کاربری/ایمیل یا کلمه عبور وارد شده نادرست است.")

        # Login with the exact spaced password should SUCCEED
        exact_res = self.client.post(reverse("accounts:login"), {
            "username_or_email": "sara.spaced@example.com",
            "password": spaced_password,
        })
        self.assertEqual(exact_res.status_code, 302)
        user = User.objects.get(email="sara.spaced@example.com")
        self.assertEqual(int(self.client.session.get("_auth_user_id")), user.pk)

    def test_inactive_user_cannot_login(self):
        """
        Inactive users (is_active=False) cannot authenticate or log in,
        and receive a specific inactive account message (R28).
        """
        inactive_user = User.objects.create_user(
            username="inactive_seller",
            email="inactive@example.com",
            password="Password123!",
            is_active=False,
        )
        Business.objects.create(user=inactive_user, name="فروشگاه غیرفعال")

        res = self.client.post(reverse("accounts:login"), {
            "username_or_email": "inactive@example.com",
            "password": "Password123!",
        })
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "حساب کاربری شما غیرفعال است.")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_invalid_email_format_rejected(self):
        """
        Email validation must reject syntactically invalid emails with a descriptive Persian error.
        """
        post_data = {
            "first_name": "تست",
            "last_name": "تستی",
            "email": "invalid-email-address",
            "password": "Password123!",
            "business_name": "کسب‌وکار",
        }
        res = self.client.post(reverse("accounts:signup"), post_data)
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "فرمت آدرس ایمیل وارد شده نامعتبر است.")
        self.assertFalse(User.objects.filter(first_name="تست").exists())

    def test_atomic_signup_rolls_back_user_on_business_error(self):
        """
        If Business creation fails, the atomic transaction must roll back User creation,
        leaving no orphaned users in the database.
        """
        with patch("apps.businesses.models.Business.objects.create", side_effect=RuntimeError("Simulated Business DB Failure")):
            post_data = {
                "first_name": "مهدی",
                "last_name": "کریمی",
                "email": "mehdi.rollback@example.com",
                "password": "Password123!",
                "business_name": "کسب‌وکار ناموفق",
            }
            res = self.client.post(reverse("accounts:signup"), post_data)
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, "خطایی در ثبت‌نام رخ داد")

            # Crucial invariant: user must NOT exist in DB
            self.assertFalse(User.objects.filter(email="mehdi.rollback@example.com").exists())

    def test_login_brute_force_throttle(self):
        """
        Multiple consecutive failed login attempts trigger the auth throttle.
        """
        # 5 failed attempts
        for _ in range(5):
            res = self.client.post(reverse("accounts:login"), {
                "username_or_email": "random@example.com",
                "password": "WrongPassword123!",
            })
            self.assertContains(res, "نام کاربری/ایمیل یا کلمه عبور وارد شده نادرست است.")

        # 6th attempt must be throttled
        throttle_res = self.client.post(reverse("accounts:login"), {
            "username_or_email": "random@example.com",
            "password": "WrongPassword123!",
        })
        self.assertContains(throttle_res, "تعداد تلاش‌های ناموفق بیش از حد مجاز است")
