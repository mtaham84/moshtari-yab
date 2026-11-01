import json
import logging
import os
from pathlib import Path
import stat
import tempfile
from unittest.mock import patch
from django.core.management import call_command
from django.test import TestCase
from cryptography.fernet import Fernet

from apps.discovery import accounts_pool_file as pool
from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount
from need_engine.crypto import _PREFIX, encrypt_secret


class SecretCutoverAndBackupProtectionTests(TestCase):
    """
    P02.10 Test Suite:
    - Active secret cutover: snapshot() defaults to encrypted serialization.
    - Backup protection: sanitize_backup_files() converts legacy on-disk plaintext backups.
    - Observable plaintext fallback: emits warning on legacy reads.
    - Strict cutover mode: raises ValueError when allow_plaintext=False.
    - Roll-forward keyring: maintains multi-key decryption.
    """

    def setUp(self):
        self.key1 = Fernet.generate_key().decode("ascii")
        self.key2 = Fernet.generate_key().decode("ascii")

    def test_snapshot_defaults_to_encrypted_serialization(self):
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            TwitterEngineAccount.objects.create(
                username="x_cutover_user",
                auth_token="sensitive_auth_token_value",
                ct0="sensitive_ct0_value",
                password="sensitive_pass",
            )
            InstagramEngineAccount.objects.create(
                username="ig_cutover_user",
                password="sensitive_ig_pass",
                role="SENDER",
            )

            # snapshot() with default arguments
            data = pool.snapshot()

            x_row = next(r for r in data["twitter"] if r["username"] == "x_cutover_user")
            ig_row = next(r for r in data["instagram"] if r["username"] == "ig_cutover_user")

            # Secrets must NOT appear in plaintext anywhere in the serialized data
            self.assertTrue(x_row["auth_token"].startswith(_PREFIX))
            self.assertTrue(x_row["ct0"].startswith(_PREFIX))
            self.assertTrue(x_row["password"].startswith(_PREFIX))
            self.assertTrue(ig_row["password"].startswith(_PREFIX))

            self.assertNotIn("sensitive_auth_token_value", json.dumps(data))
            self.assertNotIn("sensitive_ig_pass", json.dumps(data))

    def test_sanitize_backup_files_secures_existing_disk_backups(self):
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            tmp = Path(tempfile.mkdtemp())
            pool_json = tmp / "accounts_pool.json"
            hist_dir = tmp / "history"
            hist_dir.mkdir(parents=True, exist_ok=True)
            hist_file = hist_dir / "accounts_pool.20250101T000000Z.json"

            legacy_data = {
                "version": 1,
                "instagram": [{"username": "old_bot", "password": "unencrypted_old_pw"}],
                "twitter": [{"username": "old_x", "auth_token": "plain_x_tok", "ct0": "plain_x_ct0"}],
            }
            pool_json.write_text(json.dumps(legacy_data))
            hist_file.write_text(json.dumps(legacy_data))

            with patch.dict(os.environ, {"ACCOUNTS_POOL_FILE": str(pool_json), "ACCOUNTS_POOL_BACKUP_DIR": str(tmp)}):
                stats = pool.sanitize_backup_files()

            self.assertGreaterEqual(stats["files_scanned"], 2)
            self.assertGreaterEqual(stats["secrets_encrypted"], 6)

            # Check rewritten files
            pool_data = json.loads(pool_json.read_text())
            hist_data = json.loads(hist_file.read_text())

            self.assertTrue(pool_data["instagram"][0]["password"].startswith(_PREFIX))
            self.assertTrue(pool_data["twitter"][0]["auth_token"].startswith(_PREFIX))
            self.assertTrue(hist_data["instagram"][0]["password"].startswith(_PREFIX))

            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(os.stat(pool_json).st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(os.stat(hist_file).st_mode), 0o600)

    def test_observable_plaintext_fallback_logs_warning(self):
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            acc = TwitterEngineAccount.objects.create(
                username="x_plain_warning_test",
                auth_token="legacy_unencrypted_token",
            )
            # Querying unencrypted secret emits observable audit warning
            with self.assertLogs("security.audit", level="WARNING") as cm:
                val = acc.get_decrypted_auth_token()
                self.assertEqual(val, "legacy_unencrypted_token")
                self.assertTrue(any("OBSERVABLE_PLAINTEXT_FALLBACK" in msg for msg in cm.output))

    def test_strict_mode_rejects_unencrypted_secret(self):
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            acc = TwitterEngineAccount.objects.create(
                username="x_strict_test",
                auth_token="unencrypted_token_in_strict_env",
            )
            with self.assertRaises(ValueError) as cm:
                acc.get_decrypted_auth_token(allow_plaintext=False)
            self.assertIn("Plaintext fallback disabled", str(cm.exception))

    def test_roll_forward_keyring_supports_multi_key_rotation(self):
        """
        Roll-forward runbook test:
        Records encrypted with key1 can still be decrypted when key2 is active
        and key1 is placed in FIELD_ENCRYPTION_OLD_KEYS.
        """
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key1}):
            acc = TwitterEngineAccount.objects.create(username="x_rotation_user")
            acc.set_encrypted_auth_token("token_encrypted_under_key1")
            acc.save()

        # Rotate key: key2 becomes active, key1 is in old keys
        with patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": self.key2, "FIELD_ENCRYPTION_OLD_KEYS": self.key1}):
            reloaded = TwitterEngineAccount.objects.get(username="x_rotation_user")
            self.assertEqual(reloaded.get_decrypted_auth_token(), "token_encrypted_under_key1")

            # Writing new secret uses new active key (key2)
            reloaded.set_encrypted_auth_token("new_token_under_key2")
            reloaded.save()

            reloaded.refresh_from_db()
            self.assertEqual(reloaded.get_decrypted_auth_token(), "new_token_under_key2")
