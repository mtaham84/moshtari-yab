import os
import unittest
from unittest import mock
from workers.telegram_config_bot import (
    is_authorized_message,
    process_update,
    is_rate_limited,
    sanitize_log_text,
    _RATE_LIMIT_STORE,
)

class TelegramConfigBotAuthTestCase(unittest.TestCase):
    def setUp(self):
        _RATE_LIMIT_STORE.clear()

    def test_empty_config_fails_closed(self):
        """When allowlist configuration is empty/missing, all updates must be rejected."""
        with mock.patch.dict(os.environ, {"TG_ADMIN_USER_IDS": "", "TG_ALLOWED_CHAT_IDS": ""}):
            msg = {"from": {"id": 12345}, "chat": {"id": 67890}, "text": "/status"}
            auth, reason = is_authorized_message(msg)
            self.assertFalse(auth)
            self.assertEqual(reason, "unconfigured_admin_allowlist")

            update = {"update_id": 1, "message": msg}
            with mock.patch("workers.telegram_config_bot.handle_status") as mock_status:
                processed = process_update(update)
                self.assertFalse(processed)
                mock_status.assert_not_called()

    def test_non_admin_in_allowed_chat_rejected(self):
        """Non-admin user in an allowed chat is rejected and no handler executes."""
        with mock.patch.dict(os.environ, {"TG_ADMIN_USER_IDS": "99999", "TG_ALLOWED_CHAT_IDS": "-100111222"}):
            msg = {"from": {"id": 12345}, "chat": {"id": -100111222}, "text": "/status"}
            auth, reason = is_authorized_message(msg)
            self.assertFalse(auth)
            self.assertEqual(reason, "unauthorized_user")

            update = {"update_id": 2, "message": msg}
            with mock.patch("workers.telegram_config_bot.handle_status") as mock_status:
                processed = process_update(update)
                self.assertFalse(processed)
                mock_status.assert_not_called()

    def test_admin_in_unauthorized_chat_rejected(self):
        """Admin user attempting commands from an unauthorized chat is rejected."""
        with mock.patch.dict(os.environ, {"TG_ADMIN_USER_IDS": "99999", "TG_ALLOWED_CHAT_IDS": "-100111222"}):
            msg = {"from": {"id": 99999}, "chat": {"id": 55555}, "text": "/status"}
            auth, reason = is_authorized_message(msg)
            self.assertFalse(auth)
            self.assertEqual(reason, "unauthorized_chat")

            update = {"update_id": 3, "message": msg}
            with mock.patch("workers.telegram_config_bot.handle_status") as mock_status:
                processed = process_update(update)
                self.assertFalse(processed)
                mock_status.assert_not_called()

    def test_authorized_admin_in_allowed_chat_executes_handler(self):
        """Authorized admin in an authorized chat executes the appropriate handler."""
        with mock.patch.dict(os.environ, {"TG_ADMIN_USER_IDS": "99999", "TG_ALLOWED_CHAT_IDS": "-100111222"}):
            msg = {"from": {"id": 99999}, "chat": {"id": -100111222}, "text": "/status"}
            auth, reason = is_authorized_message(msg)
            self.assertTrue(auth)
            self.assertEqual(reason, "authorized")

            update = {"update_id": 4, "message": msg}
            with mock.patch("workers.telegram_config_bot.handle_status") as mock_status:
                processed = process_update(update)
                self.assertTrue(processed)
                mock_status.assert_called_once_with(-100111222)

    def test_callback_query_unauthorized_rejected(self):
        """Unauthorized callback_query updates are blocked fail-closed."""
        with mock.patch.dict(os.environ, {"TG_ADMIN_USER_IDS": "99999", "TG_ALLOWED_CHAT_IDS": "-100111222"}):
            cb_update = {
                "update_id": 5,
                "callback_query": {
                    "id": "cb1",
                    "from": {"id": 44444},
                    "message": {"chat": {"id": -100111222}, "text": "/prune"},
                    "data": "/prune",
                }
            }
            with mock.patch("workers.telegram_config_bot.handle_prune") as mock_prune:
                processed = process_update(cb_update)
                self.assertFalse(processed)
                mock_prune.assert_not_called()

    def test_rate_limiting(self):
        """Exceeding rate limit triggers throttling."""
        user_id = 99999
        for _ in range(10):
            self.assertFalse(is_rate_limited(user_id))
        # 11th call within the window should be rate-limited
        self.assertTrue(is_rate_limited(user_id))

    def test_sanitize_log_text_redacts_secrets(self):
        """Ensures bot token and proxy credentials are redacted from logs."""
        with mock.patch.dict(os.environ, {"CONFIG_BOT_TOKEN": "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"}):
            raw = (
                "Bot started with 123456789:ABCdefGHIjklMNOpqrsTUVwxyz and "
                "socks5h://admin:SuperSecretPass@127.0.0.1:1080 and "
                "vless://96b158fc-1234-5678-90ab-cdef12345678@example.com:443"
            )
            cleaned = sanitize_log_text(raw)
            self.assertNotIn("123456789:ABCdefGHIjklMNOpqrsTUVwxyz", cleaned)
            self.assertIn("[REDACTED_BOT_TOKEN]", cleaned)
            self.assertNotIn("SuperSecretPass", cleaned)
            self.assertNotIn("96b158fc-1234-5678-90ab-cdef12345678", cleaned)
            self.assertIn("[REDACTED_AUTH]", cleaned)
