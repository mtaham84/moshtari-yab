import os
from unittest.mock import patch
from django.test import SimpleTestCase
from cryptography.fernet import Fernet

from need_engine.crypto import (
    encrypt_secret,
    decrypt_secret,
    reencrypt_secret,
    is_encrypted,
    _PREFIX,
)


class SecretMigrationDesignTests(SimpleTestCase):
    """
    P02.07 Test Suite:
    Validates cryptographic primitives, key rotation, fail-closed error handling,
    and synthetic restore behavior for secret migration.
    """

    def setUp(self):
        self.key1 = Fernet.generate_key().decode("ascii")
        self.key2 = Fernet.generate_key().decode("ascii")
        self.key3 = Fernet.generate_key().decode("ascii")

    def test_encrypt_decrypt_round_trip(self):
        """Standard secret encryption and decryption round-trip."""
        secret = "SuperSecret_Instagram_Password_123!@#"
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            encrypted = encrypt_secret(secret)
            self.assertTrue(encrypted.startswith(_PREFIX))
            self.assertTrue(is_encrypted(encrypted))
            self.assertNotEqual(encrypted, secret)

            decrypted = decrypt_secret(encrypted)
            self.assertEqual(decrypted, secret)

    def test_key_rotation_with_old_keys(self):
        """
        When primary key rotates, data encrypted with older keys is still decrypted
        via FIELD_ENCRYPTION_OLD_KEYS, and re-encrypted with the new key.
        """
        secret = "RotateMe_Twitter_Auth_Token_XYZ"

        # 1. Encrypt with Key 1
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            enc_with_key1 = encrypt_secret(secret)

        # 2. Rotate to Key 2, keeping Key 1 in OLD_KEYS
        with patch.dict(os.environ, {
            "FIELD_ENCRYPTION_KEY": self.key2,
            "FIELD_ENCRYPTION_OLD_KEYS": self.key1,
        }):
            # Must decrypt successfully using Key 1 from old keys
            decrypted = decrypt_secret(enc_with_key1)
            self.assertEqual(decrypted, secret)

            # Re-encrypt with Key 2
            re_encrypted = reencrypt_secret(enc_with_key1)
            self.assertNotEqual(re_encrypted, enc_with_key1)
            self.assertEqual(decrypt_secret(re_encrypted), secret)

        # 3. Verify that re_encrypted data is decryptable with Key 2 alone (no old keys)
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key2, "FIELD_ENCRYPTION_OLD_KEYS": ""}):
            self.assertEqual(decrypt_secret(re_encrypted), secret)

    def test_missing_matching_key_fails_closed(self):
        """
        If ciphertext cannot be decrypted because the matching key is absent,
        it must fail closed (raise RuntimeError) rather than returning corrupt/raw data.
        """
        secret = "Sensitive_Credential"
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            ciphertext = encrypt_secret(secret)

        # Attempt decryption with unrelated Key 3
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key3, "FIELD_ENCRYPTION_OLD_KEYS": ""}):
            with self.assertRaises(RuntimeError) as ctx:
                decrypt_secret(ciphertext)
            self.assertIn("cannot be decrypted", str(ctx.exception))

    def test_unencrypted_legacy_fallback(self):
        """
        Legacy plaintext values not yet starting with enc:v1: are returned as-is
        during the transitional phase.
        """
        raw_legacy = "my_plain_legacy_token"
        self.assertFalse(is_encrypted(raw_legacy))
        self.assertEqual(decrypt_secret(raw_legacy), raw_legacy)

    def test_synthetic_backup_restore_cycle(self):
        """
        Synthetic restore test: encrypted dictionary representing accounts_pool payload
        can be safely serialized, inspected, and decrypted.
        """
        synthetic_account = {
            "username": "bot_synthetic_1",
            "password": "SyntheticPass!",
            "auth_token": "token_abc123",
            "ct0": "ct0_def456",
        }
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            encrypted_payload = {
                "username": synthetic_account["username"],
                "password": encrypt_secret(synthetic_account["password"]),
                "auth_token": encrypt_secret(synthetic_account["auth_token"]),
                "ct0": encrypt_secret(synthetic_account["ct0"]),
            }

            # Verify no plaintext secrets in serialized form
            for field in ("password", "auth_token", "ct0"):
                self.assertTrue(encrypted_payload[field].startswith(_PREFIX))
                self.assertNotIn(synthetic_account[field], encrypted_payload[field])

            # Decrypt during restore
            restored = {
                k: (decrypt_secret(v) if is_encrypted(v) else v)
                for k, v in encrypted_payload.items()
            }
            self.assertEqual(restored, synthetic_account)
