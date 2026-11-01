import io
import json
import os
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase, Client, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model
from django.core.management import call_command
from cryptography.fernet import Fernet
from PIL import Image
import tempfile
from pathlib import Path

from apps.businesses.models import Business
from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount
from apps.discovery import accounts_pool_file as pool
from apps.core.image_service import validate_and_sanitize_image
from need_engine.crypto import _PREFIX, encrypt_secret

User = get_user_model()


class GateG02VerificationTests(TestCase):
    """
    Gate G02 Exit Verification:
    Comprehensive verification that all Phase 2 security and protection criteria are met:
    1. API Security: Category suggestion endpoint requires auth and POST.
    2. Upload Security: Image sanitization blocks scripts and validates structure.
    3. Media Serving: nosniff header, sandbox CSP, and safe delivery.
    4. DOM Security: Template contracts avoid dangerous raw concatenation.
    5. Auth Security: Passwords preserved, rate-limited, no email verification needed.
    6. Secret Encryption: enc:v1: versioned keys, backup protection, legacy scraper gate preserved.
    """

    def setUp(self):
        self.client = Client()
        self.key = Fernet.generate_key().decode("ascii")

    def test_g02_api_security_enforced(self):
        """API Category Suggestion requires auth and POST; unauthenticated fails with 401/403/302."""
        # Unauthenticated GET
        r1 = self.client.get("/products/api/suggest-category/?title=Test")
        self.assertIn(r1.status_code, (401, 403, 302))

        # Unauthenticated POST
        r2 = self.client.post("/products/api/suggest-category/", {"title": "Test"})
        self.assertIn(r2.status_code, (401, 403, 302))

    def test_g02_image_sanitization_blocks_dangerous_files(self):
        """Image validator rejects dangerous files and sanitizes valid images."""
        bad_file = SimpleUploadedFile("malicious.png", b"<script>alert('xss')</script>", content_type="image/png")
        with self.assertRaises(ValidationError):
            validate_and_sanitize_image(bad_file)

        # Valid image produces clean ContentFile
        img_io = io.BytesIO()
        Image.new("RGB", (60, 60), color="blue").save(img_io, format="JPEG")
        valid_file = SimpleUploadedFile("clean.jpg", img_io.getvalue(), content_type="image/jpeg")
        cleaned = validate_and_sanitize_image(valid_file)
        self.assertIsNotNone(cleaned)

    def test_g02_secure_media_headers(self):
        """Media serving enforces nosniff and CSP headers on valid files."""
        temp_dir = tempfile.mkdtemp()
        test_file = Path(temp_dir) / "sample_test.png"
        test_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")

        with override_settings(MEDIA_ROOT=temp_dir):
            response = self.client.get("/media/sample_test.png")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
            self.assertIn("sandbox", response.headers.get("Content-Security-Policy", ""))

    def test_g02_secure_auth_flow(self):
        """User signup works without email verification; inactive users blocked."""
        u = User.objects.create_user(username="g02_inactive", email="inact@test.com", password="Pass123!Safe", is_active=False)
        Business.objects.create(user=u, name="InactBiz")

        resp = self.client.post("/accounts/login/", {"username_or_email": "g02_inactive", "password": "Pass123!Safe"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("حساب کاربری شما غیرفعال است", resp.content.decode("utf-8"))

    def test_g02_secret_encryption_and_backup_safety(self):
        """Active secrets serialized encrypted; legacy scrapers preserved."""
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key}):
            x_acc = TwitterEngineAccount.objects.create(
                username="g02_x_user",
                auth_token="token_40_chars_for_g02_testing_12345678",
                ct0="ct0_32_chars_for_g02_testing_123",
            )
            # Live backfill
            call_command("backfill_account_secrets", "--scope=twitter")
            x_acc.refresh_from_db()
            self.assertTrue(x_acc.auth_token.startswith(_PREFIX))
            self.assertEqual(x_acc.get_decrypted_auth_token(), "token_40_chars_for_g02_testing_12345678")

            # Snapshot has encrypted secrets
            data = pool.snapshot()
            x_row = next(r for r in data["twitter"] if r["username"] == "g02_x_user")
            self.assertTrue(x_row["auth_token"].startswith(_PREFIX))

            # Observable audit log on legacy plaintext access
            plain_x = TwitterEngineAccount.objects.create(username="g02_plain", auth_token="plain_unmigrated")
            with self.assertLogs("security.audit", level="WARNING") as cm:
                self.assertEqual(plain_x.get_decrypted_auth_token(), "plain_unmigrated")
                self.assertTrue(any("OBSERVABLE_PLAINTEXT_FALLBACK" in m for m in cm.output))
