import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.discovery.channel_config_automation import (
    normalize_channel_username,
    get_monitored_channels,
    add_monitored_channel,
    delete_monitored_channel,
    toggle_monitored_channel,
    extract_configs_from_text,
    fetch_channel_messages,
    load_channel_state,
    save_channel_state,
    sync_channel_configs,
    sync_all_active_channels,
)


class ChannelConfigAutomationUnitTests(SimpleTestCase):
    def setUp(self):
        # Never touch the real data/proxy_channels.json or channel state file from tests.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, filename in (("CHANNELS_JSON_FILE", "proxy_channels.json"),
                               ("STATE_FILE", "channel_configs_state.json")):
            patcher = patch(f"apps.discovery.channel_config_automation.{name}", Path(tmp.name) / filename)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_normalize_channel_username_formats(self):
        """Verifies channel URL cleaning and extraction."""
        test_cases = [
            ("@AR14N24B", "AR14N24B"),
            ("https://t.me/AR14N24B", "AR14N24B"),
            ("https://t.me/s/AR14N24B/", "AR14N24B"),
            ("t.me/my_proxy_channel", "my_proxy_channel"),
            ("custom_channel_123", "custom_channel_123"),
        ]
        for raw, expected in test_cases:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_channel_username(raw), expected)

        # Invalid username should raise ValueError
        with self.assertRaises(ValueError):
            normalize_channel_username("ab")  # Too short

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

    def test_channel_crud_operations(self):
        """Tests adding, toggling, and deleting monitored channels in fallback JSON mode."""
        from unittest.mock import mock_open
        fake_store = []

        with patch("apps.discovery.channel_config_automation._load_channels_json", return_value=fake_store), \
             patch("apps.discovery.channel_config_automation._save_channels_json") as mock_save:

            # Add channel
            added = add_monitored_channel("https://t.me/test_configs_chan", title="تست کانفیگ")
            self.assertEqual(added["channel_username"], "test_configs_chan")
            self.assertEqual(added["title"], "تست کانفیگ")
            self.assertTrue(added["is_active"])

            # Toggle channel
            fake_store.append(added)
            toggled = toggle_monitored_channel(added["id"])
            self.assertFalse(toggled["is_active"])

            # Delete channel
            deleted = delete_monitored_channel(added["id"])
            self.assertTrue(deleted)

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
