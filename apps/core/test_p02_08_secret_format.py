import os
from unittest.mock import patch
from django.test import TestCase
from cryptography.fernet import Fernet

from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount
from apps.discovery.accounts_pool_file import snapshot
from need_engine.crypto import _PREFIX, encrypt_secret


class IndependentSecretFormatTests(TestCase):
    """
    P02.08 Test Suite:
    Validates independent secret encryption format for active models (InstagramEngineAccount,
    TwitterEngineAccount), secure serialization in accounts_pool snapshot, transitional
    read support, and fail-closed key behavior.
    """

    def setUp(self):
        self.key1 = Fernet.generate_key().decode("ascii")
        self.key2 = Fernet.generate_key().decode("ascii")

    def test_instagram_account_encrypted_password_round_trip(self):
        """
        InstagramEngineAccount set_encrypted_password stores versioned ciphertext
        and get_decrypted_password returns the original secret.
        """
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            acc = InstagramEngineAccount(username="insta_enc_test", role="SENDER")
            acc.set_encrypted_password("SuperSecretInstagramPass99!")
            acc.save()

            reloaded = InstagramEngineAccount.objects.get(username="insta_enc_test")
            self.assertTrue(reloaded.password.startswith(_PREFIX))
            self.assertNotIn("SuperSecretInstagramPass99!", reloaded.password)
            self.assertEqual(reloaded.get_decrypted_password(), "SuperSecretInstagramPass99!")

    def test_twitter_account_encrypted_fields_round_trip(self):
        """
        TwitterEngineAccount encrypts password, auth_token, and ct0 securely.
        """
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            x_acc = TwitterEngineAccount(username="x_enc_test")
            x_acc.set_encrypted_password("TwitterPass123!")
            x_acc.set_encrypted_auth_token("auth_token_value_40_chars_long_1234567")
            x_acc.set_encrypted_ct0("ct0_token_value_32_chars_long_12")
            x_acc.save()

            reloaded = TwitterEngineAccount.objects.get(username="x_enc_test")
            self.assertTrue(reloaded.password.startswith(_PREFIX))
            self.assertTrue(reloaded.auth_token.startswith(_PREFIX))
            self.assertTrue(reloaded.ct0.startswith(_PREFIX))

            self.assertEqual(reloaded.get_decrypted_password(), "TwitterPass123!")
            self.assertEqual(reloaded.get_decrypted_auth_token(), "auth_token_value_40_chars_long_1234567")
            self.assertEqual(reloaded.get_decrypted_ct0(), "ct0_token_value_32_chars_long_12")

    def test_transitional_reader_handles_both_plaintext_and_encrypted(self):
        """
        get_decrypted_password safely reads legacy plaintext during transition
        as well as modern enc:v1: ciphertext.
        """
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            # Legacy plaintext record
            legacy_acc = InstagramEngineAccount.objects.create(
                username="legacy_plain_bot",
                password="unencrypted_legacy_password",
                role="SCRAPER",
            )
            self.assertEqual(legacy_acc.get_decrypted_password(), "unencrypted_legacy_password")

            # Modern encrypted record
            modern_acc = InstagramEngineAccount.objects.create(
                username="modern_enc_bot",
                password=encrypt_secret("modern_secure_password"),
                role="SENDER",
            )
            self.assertEqual(modern_acc.get_decrypted_password(), "modern_secure_password")

    def test_accounts_pool_snapshot_encrypts_secrets_for_backup(self):
        """
        accounts_pool snapshot() ensures all passwords, auth_tokens, and ct0 tokens
        are serialized as enc:v1: ciphertext to protect backup history on disk.
        """
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            InstagramEngineAccount.objects.create(
                username="pool_test_ig",
                password="raw_plain_password_in_db",
                role="SENDER",
            )
            TwitterEngineAccount.objects.create(
                username="pool_test_x",
                password="x_plain_pass",
                auth_token="x_plain_auth",
                ct0="x_plain_ct0",
            )

            data = snapshot(encrypt_secrets=True)
            ig_row = next(r for r in data["instagram"] if r["username"] == "pool_test_ig")
            x_row = next(r for r in data["twitter"] if r["username"] == "pool_test_x")

            # Verify encrypted in serialized backup
            self.assertTrue(ig_row["password"].startswith(_PREFIX))
            self.assertNotIn("raw_plain_password_in_db", ig_row["password"])

            self.assertTrue(x_row["password"].startswith(_PREFIX))
            self.assertTrue(x_row["auth_token"].startswith(_PREFIX))
            self.assertTrue(x_row["ct0"].startswith(_PREFIX))

    def test_missing_key_fails_closed_in_model_getter(self):
        """
        When the matching decryption key is absent, get_decrypted_password raises
        RuntimeError rather than returning raw ciphertext or corrupt tokens.
        """
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            acc = InstagramEngineAccount(username="fail_closed_test", role="SENDER")
            acc.set_encrypted_password("SecretPayload")
            acc.save()

        # Switch key to Key 2 with empty old keys
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key2, "FIELD_ENCRYPTION_OLD_KEYS": ""}):
            reloaded = InstagramEngineAccount.objects.get(username="fail_closed_test")
            with self.assertRaises(RuntimeError):
                reloaded.get_decrypted_password()
