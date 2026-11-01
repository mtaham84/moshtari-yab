import os
import unittest
from unittest import mock
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from xray.multi_proxy_manager import (
    get_backup_tunnel_config,
    rebuild_xray_config_file,
)

OLD_EXPOSED_SECRET = "ayC61MsT3ztDAgq5YgiMAg"
OLD_EXPOSED_IP = "87.107.104.4"

class MultiProxyManagerSecurityTestCase(unittest.TestCase):
    def test_source_code_has_no_hardcoded_secret_or_ip(self):
        """Verifies that the exposed literal secret and IP are completely absent from the source code."""
        source_path = Path("xray/multi_proxy_manager.py")
        if not source_path.exists():
            source_path = Path("/app/xray/multi_proxy_manager.py")
        text = source_path.read_text(encoding="utf-8")
        self.assertNotIn(OLD_EXPOSED_SECRET, text, "Old exposed secret must not appear in source code.")
        self.assertNotIn(OLD_EXPOSED_IP, text, "Old exposed server IP must not appear in source code.")

    def test_missing_env_returns_none_fail_closed(self):
        """When backup tunnel credentials are not in env, get_backup_tunnel_config must return None."""
        with mock.patch.dict(os.environ, {"BACKUP_TUNNEL_SERVER": "", "BACKUP_TUNNEL_PASSWORD": ""}):
            config = get_backup_tunnel_config()
            self.assertIsNone(config)

    def test_configured_env_loads_securely(self):
        """When backup tunnel is provided via env, it loads without fallback to old credentials."""
        env_vars = {
            "BACKUP_TUNNEL_SERVER": "10.0.0.99",
            "BACKUP_TUNNEL_PORT": "4433",
            "BACKUP_TUNNEL_PASSWORD": "NewRotatedSecurePassword123!",
            "BACKUP_TUNNEL_METHOD": "chacha20-ietf-poly1305",
        }
        with mock.patch.dict(os.environ, env_vars):
            config = get_backup_tunnel_config()
            self.assertIsNotNone(config)
            self.assertEqual(config["tag"], "default_proxy")
            self.assertEqual(config["protocol"], "shadowsocks")
            server_entry = config["settings"]["servers"][0]
            self.assertEqual(server_entry["address"], "10.0.0.99")
            self.assertEqual(server_entry["port"], 4433)
            self.assertEqual(server_entry["password"], "NewRotatedSecurePassword123!")

    def test_rebuild_xray_config_without_env_does_not_inject_secret(self):
        """With no active DB proxies and no env config, rebuild_xray_config_file must not inject secret."""
        tmp_cfg = Path("/tmp/xray_config_test.json")
        with mock.patch.dict(os.environ, {"BACKUP_TUNNEL_SERVER": "", "BACKUP_TUNNEL_PASSWORD": ""}), \
             mock.patch("xray.multi_proxy_manager.CONFIG_FILE", tmp_cfg):
            with mock.patch("apps.discovery.models.ProxyConfig.objects.filter") as mock_filter:
                mock_filter.return_value.order_by.return_value = []
                success = rebuild_xray_config_file()
                self.assertTrue(success)
                self.assertTrue(tmp_cfg.exists())
                written_content = tmp_cfg.read_text(encoding="utf-8")
                self.assertNotIn(OLD_EXPOSED_SECRET, written_content)
                self.assertNotIn(OLD_EXPOSED_IP, written_content)
                tmp_cfg.unlink(missing_ok=True)

