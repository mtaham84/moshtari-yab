import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.discovery.channel_config_automation import (
    AUTHORIZED_CHANNELS,
    extract_configs_from_text,
    fetch_channel_messages,
    load_channel_state,
    save_channel_state,
    sync_channel_configs,
)


class ChannelConfigAutomationUnitTests(SimpleTestCase):
    def test_strict_channel_authorization(self):
        """Security Guardrail: Only authorized channels (AR14N24B) are allowed."""
        self.assertIn("AR14N24B", AUTHORIZED_CHANNELS)

        with self.assertRaises(PermissionError):
            fetch_channel_messages("unauthorized_random_channel")

        res = sync_channel_configs("fake_channel")
        self.assertFalse(res["success"])
        self.assertIn("مجاز نمی‌باشد", res["error"])

    def test_extract_configs_from_text(self):
        sample_html = """
        <div class="tgme_widget_message_text">
            کانفیگ‌های جدید امروز:
            vless://01d31e8f-5eb5-4f2f-bceb-addb87f107a3@mabix.dgtr.net:2083?security=reality#iran
            <br>
            vmess://eyJhZGQiOiIxLjIuMy40IiwicG9ydCI6NDQzfQ==
            <br>
            ss://Y2hhY2hhMjAtaWV0Zi1wb2x5MTMwNTpwYXNz@82.38.31.208:8080#ss1
            <br>
            trojan://pass@trojan.example.com:443#tr1
            لینک‌های غیرمرتبط: https://google.com
        </div>
        """
        extracted = extract_configs_from_text(sample_html)
        self.assertEqual(len(extracted), 4)
        self.assertTrue(any(c.startswith("vless://") for c in extracted))
        self.assertTrue(any(c.startswith("vmess://") for c in extracted))
        self.assertTrue(any(c.startswith("ss://") for c in extracted))
        self.assertTrue(any(c.startswith("trojan://") for c in extracted))

    def test_state_persistence(self):
        from unittest.mock import mock_open
        fake_data = json.dumps({"AR14N24B": {"last_post_id": 46000, "total_imported": 25}})
        with patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=fake_data)):
            state = load_channel_state("AR14N24B")
            self.assertEqual(state["last_post_id"], 46000)
            self.assertEqual(state["total_imported"], 25)

    @patch("apps.discovery.channel_config_automation.fetch_channel_messages")
    @patch("xray.multi_proxy_manager.add_proxy_config")
    @patch("apps.discovery.channel_config_automation.save_channel_state")
    @patch("apps.discovery.channel_config_automation.load_channel_state")
    def test_sync_channel_configs_success(self, mock_load, mock_save, mock_add, mock_fetch):
        mock_load.return_value = {"last_post_id": 100, "total_imported": 0}

        mock_fetch.return_value = [
            {
                "channel": "AR14N24B",
                "post_id": 101,
                "html": 'vless://uuid@server:443?type=tcp#cfg1 ss://pass@server:8080#cfg2',
            }
        ]

        # First config succeeds with ping 120ms, second config fails
        mock_add.side_effect = [
            {"success": True, "protocol": "vless", "tag": "AR14_101_cfg1", "ping_ms": 120.0, "local_port": 1085},
            {"success": False, "message": "پینگ بالای ۲۰۰۰ms"},
        ]

        res = sync_channel_configs("AR14N24B", max_posts=10)
        self.assertTrue(res["success"])
        self.assertEqual(res["posts_checked"], 1)
        self.assertEqual(res["configs_found"], 2)
        self.assertEqual(res["added_count"], 1)
        self.assertEqual(res["failed_count"], 1)
        self.assertEqual(res["last_post_id"], 101)
        mock_save.assert_called_once()
