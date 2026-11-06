import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from apps.accounts import services
from shared import telegram_alerts


class TelegramAlertSettingsTests(SimpleTestCase):
    def test_proxy_candidates_are_unique_and_direct_once(self):
        env = {"TG_BOT_PROXY": "socks5h://xray:1080", "TELEGRAM_ALERT_FALLBACK_PROXIES": "socks5h://xray:1080, ,http://p:8080"}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(telegram_alerts.proxy_candidates(), ["socks5h://xray:1080", "http://p:8080", None])

    def test_empty_tg_bot_proxy_is_not_an_extra_direct_attempt(self):
        env = {"TG_BOT_PROXY": "", "TELEGRAM_ALERT_FALLBACK_PROXIES": ""}
        with mock.patch.dict(os.environ, env):
            self.assertEqual(telegram_alerts.proxy_candidates(), [None])

    def test_admin_chats_and_subscribers_come_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subs.json"
            path.write_text(json.dumps([42, "43", "junk"]), encoding="utf-8")
            env = {"TELEGRAM_ADMIN_CHAT_IDS": "7, 8", "BOT_SUBSCRIBERS_FILE": str(path)}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(services.get_subscribers(), {7, 8, 42, 43})
                telegram_alerts.save_subscribers({7, 99})
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")), [7, 99])

    def test_send_alert_reuses_the_working_route(self):
        env = {
            "CONFIG_BOT_TOKEN": "t",
            "TG_BOT_PROXY": "",
            "TELEGRAM_ALERT_FALLBACK_PROXIES": "socks5h://dead:1",
            "TELEGRAM_ADMIN_CHAT_IDS": "1,2,3",
            "BOT_SUBSCRIBERS_FILE": "/nonexistent/subs.json",
        }
        calls = []

        def fake_post(url, json=None, proxies=None, timeout=None):
            calls.append(proxies)
            if proxies:
                raise OSError("dead proxy")
            return mock.MagicMock(json=lambda: {"ok": True})

        with mock.patch.dict(os.environ, env), mock.patch("requests.post", side_effect=fake_post):
            self.assertTrue(services.send_telegram_alert("hi"))
        # dead proxy is tried only for the first chat; the other two go direct
        self.assertEqual(calls.count(None), 3)
        self.assertEqual(len(calls), 4)

    def test_no_token_skips(self):
        with mock.patch.dict(os.environ, {"CONFIG_BOT_TOKEN": ""}):
            self.assertFalse(services.send_telegram_alert("hi"))
