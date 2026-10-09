from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from apps.businesses.models import Business
from .models import EmailVerification

User = get_user_model()

class AuthAndOnboardingTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.signup_url = reverse("accounts:signup")
        self.verify_url = reverse("accounts:verify")
        self.login_url = reverse("accounts:login")
        self.logout_url = reverse("accounts:logout")
        self.dashboard_url = reverse("accounts:dashboard")

    def test_signup_page_loads(self):
        response = self.client.get(self.signup_url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/signup.html")
        self.assertIn("ثبت‌نام و معرفی کسب‌وکار", response.content.decode("utf-8"))

    def _signup_data(self, **kw):
        data = {
            "first_name": "سارا",
            "last_name": "محمدی",
            "email": "sara.m@example.com",
            "password": "secret-pass-1",
            "business_name": "آکادمی کدنویسی سارا",
            "business_type": "SERVICE",
            "business_domain": "آموزش و برنامه‌نویسی",
        }
        data.update(kw)
        return data

    def test_signup_creates_account_and_logs_in_without_email(self):
        response = self.client.post(self.signup_url, self._signup_data(), follow=True)
        self.assertTemplateUsed(response, "accounts/dashboard.html")
        user = User.objects.get(email="sara.m@example.com")
        self.assertTrue(user.check_password("secret-pass-1"))
        self.assertEqual(Business.objects.get(user=user).business_type, "SERVICE")
        self.assertEqual(len(mail.outbox), 0)
        self.client.logout()
        self.assertTrue(self.client.login(username="sara.m@example.com", password="secret-pass-1"))

    def test_signup_cannot_take_over_an_existing_account(self):
        owner = User.objects.create_user(username="owner@example.com", email="owner@example.com", password="original-pass")
        response = self.client.post(self.signup_url, self._signup_data(email="Owner@example.com", password="attacker-pass"))
        self.assertTemplateUsed(response, "accounts/signup.html")
        owner.refresh_from_db()
        self.assertTrue(owner.check_password("original-pass"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_signup_requires_a_password(self):
        self.client.post(self.signup_url, self._signup_data(password=""))
        self.assertFalse(User.objects.filter(email="sara.m@example.com").exists())

    def test_verification_success_creates_user_and_business(self):
        # Create pending verification
        verification = EmailVerification.objects.create(
            email="ali.reza@example.com",
            code="654321",
            session_data={
                "first_name": "علی",
                "last_name": "رضایی",
                "business_name": "پوشاک دنیم استایل",
                "business_type": "PHYSICAL",
                "business_domain": "پوشاک و مد",
            },
            expires_at=timezone.now() + timedelta(minutes=10)
        )

        session = self.client.session
        session["pending_verification_id"] = verification.id
        session["pending_verification_email"] = verification.email
        session.save()

        # Submit correct code
        response = self.client.post(self.verify_url, {"code": "654321"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/dashboard.html")

        # Verify User was created
        user = User.objects.filter(email="ali.reza@example.com").first()
        self.assertIsNotNone(user)
        self.assertEqual(user.first_name, "علی")
        self.assertEqual(user.last_name, "رضایی")

        # Verify Business was created and linked
        business = Business.objects.filter(user=user).first()
        self.assertIsNotNone(business)
        self.assertEqual(business.name, "پوشاک دنیم استایل")
        self.assertEqual(business.business_type, "PHYSICAL")
        self.assertEqual(business.business_domain, "پوشاک و مد")

        # Verify EmailVerification status
        verification.refresh_from_db()
        self.assertTrue(verification.is_verified)

    def test_brute_force_protection_blocks_after_5_failed_attempts(self):
        verification = EmailVerification.objects.create(
            email="test.brute@example.com",
            code="112233",
            session_data={"first_name": "تست", "last_name": "کاربر"},
            expires_at=timezone.now() + timedelta(minutes=10)
        )

        session = self.client.session
        session["pending_verification_id"] = verification.id
        session["pending_verification_email"] = verification.email
        session.save()

        # Try 4 wrong attempts
        for i in range(1, 5):
            response = self.client.post(self.verify_url, {"code": "000000"})
            verification.refresh_from_db()
            self.assertEqual(verification.attempts, i)
            self.assertFalse(verification.is_blocked()[0])

        # 5th wrong attempt triggers block
        response = self.client.post(self.verify_url, {"code": "000000"})
        verification.refresh_from_db()
        self.assertEqual(verification.attempts, 5)
        is_blocked, remaining = verification.is_blocked()
        self.assertTrue(is_blocked)
        self.assertGreater(remaining, 0)
        self.assertLessEqual(remaining, 120)

        # 6th attempt should be blocked and rejected
        response = self.client.post(self.verify_url, {"code": "112233"})
        self.assertIn("مسدود", response.content.decode("utf-8"))

    def test_dashboard_requires_login(self):
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.login_url, response.url)

    def test_logout(self):
        user = User.objects.create_user(username="u1@example.com", email="u1@example.com")
        self.client.force_login(user)
        response = self.client.get(self.logout_url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/landing.html")

    def test_password_login_success(self):
        user = User.objects.create_user(
            username="seller@moshtariyab.com",
            email="seller@moshtariyab.com",
            first_name="آرش",
            password="demo123456"
        )
        response = self.client.post(self.login_url, {
            "auth_method": "password",
            "username_or_email": "seller@moshtariyab.com",
            "password": "demo123456",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/dashboard.html")
        self.assertTrue(response.context["user"].is_authenticated)

    def test_password_login_failure(self):
        User.objects.create_user(
            username="seller2@moshtariyab.com",
            email="seller2@moshtariyab.com",
            password="correctpassword"
        )
        response = self.client.post(self.login_url, {
            "auth_method": "password",
            "username_or_email": "seller2@moshtariyab.com",
            "password": "wrongpassword",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")
        self.assertIn("نادرست", response.content.decode("utf-8"))

    def test_otp_login_request(self):
        User.objects.create_user(
            username="seller3@moshtariyab.com",
            email="seller3@moshtariyab.com",
        )
        response = self.client.post(self.login_url, {
            "auth_method": "otp",
            "email": "seller3@moshtariyab.com",
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/verify.html")
        self.assertTrue(EmailVerification.objects.filter(email="seller3@moshtariyab.com").exists())

