from __future__ import annotations

import logging
import os
import time
import unittest
from unittest.mock import patch

from shared.tele_logging.formatter import (
    ROOT_CAUSE_ANALYSIS_MAP,
    format_error_alert,
    get_clean_path,
    get_tehran_now,
    sanitize_text,
)
from shared.tele_logging.handler import AsyncTelegramHandler
from shared.tele_logging.sender import get_bot_token, get_target_chat_ids, send_telegram_raw
from shared.tele_logging.throttler import ErrorThrottler


class TestErrorThrottler(unittest.TestCase):
    def setUp(self) -> None:
        self.throttler = ErrorThrottler(cooldown_seconds=1.0)

    def test_first_occurrence_emits(self) -> None:
        fp = ErrorThrottler.compute_fingerprint("apps/test.py", 10, "ValueError", "bad input")
        decision = self.throttler.evaluate(fp)
        self.assertTrue(decision.should_send)
        self.assertEqual(decision.count, 1)

    def test_subsequent_occurrence_suppressed(self) -> None:
        fp = ErrorThrottler.compute_fingerprint("apps/test.py", 10, "ValueError", "bad input")
        self.throttler.evaluate(fp)
        decision2 = self.throttler.evaluate(fp)
        self.assertFalse(decision2.should_send)
        self.assertEqual(decision2.count, 2)

    def test_different_fingerprints_independent(self) -> None:
        fp1 = ErrorThrottler.compute_fingerprint("apps/test.py", 10, "ValueError", "bad input")
        fp2 = ErrorThrottler.compute_fingerprint("apps/test.py", 20, "KeyError", "missing key")
        self.assertTrue(self.throttler.evaluate(fp1).should_send)
        self.assertTrue(self.throttler.evaluate(fp2).should_send)

    def test_cooldown_reset(self) -> None:
        throttler = ErrorThrottler(cooldown_seconds=0.05)
        fp = ErrorThrottler.compute_fingerprint("apps/test.py", 10, "ValueError")
        self.assertTrue(throttler.evaluate(fp).should_send)
        self.assertFalse(throttler.evaluate(fp).should_send)
        time.sleep(0.06)
        decision = throttler.evaluate(fp)
        self.assertTrue(decision.should_send)
        self.assertEqual(decision.count, 1)


class TestFormatter(unittest.TestCase):
    def test_tehran_time(self) -> None:
        greg, jalali = get_tehran_now()
        self.assertTrue(len(greg) >= 19)
        self.assertTrue(len(jalali) >= 10)

    def test_clean_path(self) -> None:
        self.assertEqual(
            get_clean_path("/app/apps/discovery/outreach_service.py"),
            "apps/discovery/outreach_service.py",
        )
        self.assertEqual(
            get_clean_path("/home/javad/Projects/moshtari-yab/shared/telegram_alerts.py"),
            "shared/telegram_alerts.py",
        )

    def test_sanitize_text(self) -> None:
        text = "Error connecting with password='SuperSecret123' and token=abc-999-xyz"
        clean = sanitize_text(text)
        self.assertNotIn("SuperSecret123", clean)
        self.assertNotIn("abc-999-xyz", clean)
        self.assertIn("***MASKED***", clean)

    def test_sanitize_database_url(self) -> None:
        url = "Failed postgres://myuser:P@ssword123@db:5432/mydb connection"
        clean = sanitize_text(url)
        self.assertNotIn("P@ssword123", clean)
        self.assertIn("myuser:***@db:5432/mydb", clean)

    def test_root_cause_diagnosis_presence(self) -> None:
        record_dict = {
            "levelname": "ERROR",
            "module": "outreach_service",
            "pathname": "/app/apps/discovery/outreach_service.py",
            "lineno": 150,
            "funcName": "send_dm",
            "message": "Connection aborted by proxy",
            "exc_type": "ConnectionError",
            "exc_msg": "socks5h://xray:1080 connection refused",
            "exc_tb": None,
            "request_info": None,
        }
        msg = format_error_alert(record_dict, fingerprint="err_123", repeat_count=1)
        self.assertIn("ConnectionError", msg)
        self.assertIn(ROOT_CAUSE_ANALYSIS_MAP["ConnectionError"], msg)
        self.assertIn("apps/discovery/outreach_service.py:150", msg)
        self.assertIn("send_dm", msg)
        self.assertIn("#err_123", msg)

    def test_compact_format_with_request_info(self) -> None:
        record_dict = {
            "levelname": "ERROR",
            "module": "views",
            "pathname": "/app/apps/discovery/views/opportunities.py",
            "lineno": 185,
            "funcName": "opportunity_detail",
            "message": "Error 500",
            "exc_type": "IntegrityError",
            "exc_msg": "duplicate key value violates unique constraint\nDETAIL: Key (id)=(482) already exists.",
            "exc_tb": None,
            "request_info": "POST /panel/opportunities/482/ (User: admin)",
        }
        msg = format_error_alert(record_dict, fingerprint="err_482", repeat_count=3)
        self.assertIn("IntegrityError", msg)
        self.assertIn("POST /panel/opportunities/482/", msg)
        self.assertIn("User: admin", msg)
        self.assertIn("(تکرار: 3)", msg)
        # Verify it is compact (no more than 7 lines)
        self.assertLessEqual(len(msg.strip().split("\n")), 7)


class TestSender(unittest.TestCase):
    @patch.dict(os.environ, {"TELEGRAM_ERROR_BOT_TOKEN": "bot_err_123", "CONFIG_BOT_TOKEN": "bot_cfg_456"})
    def test_bot_token_precedence(self) -> None:
        self.assertEqual(get_bot_token(), "bot_err_123")

    @patch.dict(os.environ, {"TELEGRAM_ERROR_BOT_TOKEN": "", "CONFIG_BOT_TOKEN": "bot_cfg_456"})
    def test_bot_token_fallback(self) -> None:
        self.assertEqual(get_bot_token(), "bot_cfg_456")

    @patch.dict(os.environ, {"TELEGRAM_ERROR_CHAT_ID": "-100999888, 123456"})
    def test_target_chat_ids_custom(self) -> None:
        self.assertEqual(get_target_chat_ids(), [-100999888, 123456])

    @patch("requests.post", side_effect=Exception("Network down"))
    @patch.dict(os.environ, {"TELEGRAM_ERROR_BOT_TOKEN": "dummy", "TELEGRAM_ERROR_CHAT_ID": "123"})
    def test_send_raw_failsafe_no_exception(self, mock_post) -> None:
        # Must return False and NEVER raise
        result = send_telegram_raw("Sample alert")
        self.assertFalse(result)


class TestAsyncTelegramHandler(unittest.TestCase):
    def test_ignores_info_warning(self) -> None:
        handler = AsyncTelegramHandler()
        try:
            record_info = logging.LogRecord("app", logging.INFO, "file.py", 10, "msg", (), None)
            handler.emit(record_info)
            self.assertTrue(handler.queue.empty())

            record_warn = logging.LogRecord("app", logging.WARNING, "file.py", 10, "msg", (), None)
            handler.emit(record_warn)
            self.assertTrue(handler.queue.empty())
        finally:
            handler.close()

    def test_ignores_self_logging_loop(self) -> None:
        handler = AsyncTelegramHandler()
        try:
            record = logging.LogRecord(
                "shared.tele_logging.sender",
                logging.ERROR,
                "sender.py",
                50,
                "Telegram failed",
                (),
                None,
            )
            handler.emit(record)
            self.assertTrue(handler.queue.empty())
        finally:
            handler.close()

    def test_ignores_telegram_api_urllib_loop(self) -> None:
        handler = AsyncTelegramHandler()
        try:
            record = logging.LogRecord(
                "urllib3.connectionpool",
                logging.ERROR,
                "connectionpool.py",
                100,
                "Failed to establish connection to api.telegram.org",
                (),
                None,
            )
            handler.emit(record)
            self.assertTrue(handler.queue.empty())
        finally:
            handler.close()

    @patch("shared.tele_logging.handler.send_telegram_raw")
    def test_enqueues_and_delivers_error(self, mock_send) -> None:
        handler = AsyncTelegramHandler(cooldown_seconds=10.0)
        try:
            record = logging.LogRecord(
                "apps.discovery",
                logging.ERROR,
                "apps/discovery/test.py",
                42,
                "Test error message",
                (),
                None,
            )
            handler.emit(record)
            # Give background thread a short moment to process
            time.sleep(0.15)
            mock_send.assert_called_once()
            args, _ = mock_send.call_args
            self.assertIn("Test error message", args[0])
            self.assertIn("apps/discovery/test.py:42", args[0])
        finally:
            handler.close()


if __name__ == "__main__":
    unittest.main()
