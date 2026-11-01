import io
import os
from unittest.mock import patch
from django.core.management import call_command
from django.test import TestCase
from cryptography.fernet import Fernet

from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount
from need_engine.crypto import _PREFIX, decrypt_secret


class SecretBackfillCommandTests(TestCase):
    """
    P02.09 Test Suite:
    Validates batch backfill command for account secrets:
    - Dry-run count reporting without writing to DB and without printing secrets
    - Live idempotent batch encryption and round-trip verification
    - Compatibility gate preservation for legacy Instagram scrapers
    - Error quarantine without data loss / wiping
    """

    def setUp(self):
        self.key = Fernet.generate_key().decode("ascii")

    def test_dry_run_reports_counts_without_mutating_or_leaking_secrets(self):
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key}):
            secret_token = "SuperSecretTokenPlaintext123456789012"
            TwitterEngineAccount.objects.create(
                username="x_dry_run_test",
                auth_token=secret_token,
                ct0="plain_ct0_value",
            )

            out = io.StringIO()
            call_command("backfill_account_secrets", "--dry-run", "--scope=twitter", stdout=out)
            output_str = out.getvalue()

            # Ensure secret plaintext is NEVER printed in console output
            self.assertNotIn(secret_token, output_str)
            self.assertIn("Plaintext Migrated:  1", output_str)

            # Ensure database was NOT altered during dry-run
            acc = TwitterEngineAccount.objects.get(username="x_dry_run_test")
            self.assertEqual(acc.auth_token, secret_token)
            self.assertFalse(acc.auth_token.startswith(_PREFIX))

    def test_live_backfill_encrypts_and_verifies_roundtrip(self):
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key}):
            acc = TwitterEngineAccount.objects.create(
                username="x_live_test",
                auth_token="token_to_encrypt_40_chars_long_123456",
                ct0="ct0_to_encrypt_32_chars_long_12",
                password="x_plain_password_123",
            )

            out = io.StringIO()
            call_command("backfill_account_secrets", "--scope=twitter", stdout=out)
            output_str = out.getvalue()

            self.assertIn("Plaintext Migrated:  1", output_str)

            acc.refresh_from_db()
            # In DB column, must be versioned ciphertext
            self.assertTrue(acc.auth_token.startswith(_PREFIX))
            self.assertTrue(acc.ct0.startswith(_PREFIX))
            self.assertTrue(acc.password.startswith(_PREFIX))

            # Model getters decrypt back to original plaintext
            self.assertEqual(acc.get_decrypted_auth_token(), "token_to_encrypt_40_chars_long_123456")
            self.assertEqual(acc.get_decrypted_ct0(), "ct0_to_encrypt_32_chars_long_12")
            self.assertEqual(acc.get_decrypted_password(), "x_plain_password_123")

    def test_backfill_is_idempotent_and_resumable(self):
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key}):
            TwitterEngineAccount.objects.create(
                username="x_idempotent_test",
                auth_token="token_abc_123456789012345678901234567",
            )

            # First run: migrates
            call_command("backfill_account_secrets", "--scope=twitter")

            # Second run: must detect already encrypted records and make 0 changes
            out = io.StringIO()
            call_command("backfill_account_secrets", "--scope=twitter", stdout=out)
            output_str = out.getvalue()

            self.assertIn("Already Encrypted:   1", output_str)
            self.assertIn("Plaintext Migrated:  0", output_str)

    def test_legacy_scrapers_preserved_by_compatibility_gate(self):
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key}):
            # Legacy SCRAPER account (consumed by frozen instagram_engine)
            scraper = InstagramEngineAccount.objects.create(
                username="legacy_scraper_bot",
                password="plain_scraper_pass",
                role="SCRAPER",
            )
            # Active SENDER account
            sender = InstagramEngineAccount.objects.create(
                username="active_sender_bot",
                password="plain_sender_pass",
                role="SENDER",
            )

            out = io.StringIO()
            call_command("backfill_account_secrets", "--scope=instagram", stdout=out)
            output_str = out.getvalue()

            self.assertIn("Legacy Gate Preserved: 1", output_str)
            self.assertIn("Plaintext Migrated:    1", output_str)

            scraper.refresh_from_db()
            sender.refresh_from_db()

            # Scraper password preserved as plaintext for frozen legacy compatibility
            self.assertEqual(scraper.password, "plain_scraper_pass")

            # Sender password safely migrated to enc:v1:
            self.assertTrue(sender.password.startswith(_PREFIX))
            self.assertEqual(sender.get_decrypted_password(), "plain_sender_pass")

    def test_quarantine_on_error_preserves_data_without_wiping(self):
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key}):
            acc = TwitterEngineAccount.objects.create(
                username="x_quarantine_test",
                auth_token="valuable_raw_token",
            )

            # Simulate failure during encryption of valuable token
            with patch("apps.discovery.management.commands.backfill_account_secrets.encrypt_secret", side_effect=RuntimeError("Simulated KMS Failure")):
                out = io.StringIO()
                call_command("backfill_account_secrets", "--scope=twitter", stdout=out)
                output_str = out.getvalue()

            self.assertIn("Quarantined/Errors:  1", output_str)
            self.assertIn("Quarantine Notice", output_str)

            acc.refresh_from_db()
            # Must NOT wipe the secret to empty string! Plaintext remains preserved safely
            self.assertEqual(acc.auth_token, "valuable_raw_token")
