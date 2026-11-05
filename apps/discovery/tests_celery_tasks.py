"""
Unit tests for Celery distributed tasks, Redis task routing,
and automated proxy self-healing.
"""
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase

from config.celery import app as celery_app
from apps.discovery.tasks import (
    task_dispatch_instagram_outreach,
    task_sync_lead_replies,
    task_sync_telegram_channel_configs,
    task_healthcheck_and_heal_proxies,
)


class CeleryTaskQueueTestCase(SimpleTestCase):
    def test_celery_app_initialized(self):
        """Verify Celery application instance is properly configured."""
        self.assertEqual(celery_app.main, "moshtari_yab")
        self.assertIn("apps.discovery.tasks.task_dispatch_instagram_outreach", celery_app.tasks)
        self.assertIn("apps.discovery.tasks.task_sync_lead_replies", celery_app.tasks)
        self.assertIn("apps.discovery.tasks.task_sync_telegram_channel_configs", celery_app.tasks)
        self.assertIn("apps.discovery.tasks.task_healthcheck_and_heal_proxies", celery_app.tasks)

    @patch("apps.discovery.tasks.HumanShiftPlanner.is_shift_active")
    def test_task_dispatch_outreach_respects_shift_pause(self, mock_shift):
        """Task should return paused without sending DMs when shift is closed."""
        mock_shift.return_value = (False, "استراحت شبانه", 3600)
        res = task_dispatch_instagram_outreach(tenant="tenant_4", ignore_shift=False)
        self.assertTrue(res["success"])
        self.assertTrue(res["paused"])
        self.assertEqual(res["sent_count"], 0)
        self.assertIn("استراحت شبانه", res["reason"])

    @patch("apps.discovery.tasks.check_and_sync_lead_replies")
    def test_task_sync_lead_replies_executes(self, mock_sync):
        """Verify reply polling task calls underlying sync engine."""
        mock_sync.return_value = {"success": True, "replies_found": 3, "details": []}
        res = task_sync_lead_replies(tenant_ids=["tenant_4"])
        self.assertTrue(res["success"])
        self.assertEqual(res["replies_found"], 3)
        mock_sync.assert_called_once_with(["tenant_4"])

    @patch("apps.discovery.channel_config_automation.sync_all_active_channels")
    def test_task_sync_telegram_channels(self, mock_channels):
        """Verify channel config crawler task runs."""
        mock_channels.return_value = {"success": True, "total_channels": 2, "total_added": 4, "message": "OK"}
        res = task_sync_telegram_channel_configs(max_posts=10, max_add=5)
        self.assertTrue(res["success"])
        self.assertEqual(res["total_added"], 4)

    @patch("socket.socket")
    @patch("apps.discovery.models.proxy.ProxyConfig.objects")
    @patch("apps.discovery.models.instagram.InstagramEngineAccount.objects")
    def test_task_healthcheck_heals_dead_proxy(self, mock_acc_qs, mock_proxy_qs, mock_socket_cls):
        """
        Verify that when a sender account's proxy is dead (socket connection fails),
        the task automatically finds a healthy spare from ProxyConfig and swaps it!
        """
        # Mock socket connect failure (dead proxy)
        mock_sock_instance = MagicMock()
        mock_sock_instance.connect_ex.return_value = 111  # Connection refused
        mock_socket_cls.return_value = mock_sock_instance

        # Mock sender account with dead proxy
        sender_acc = MagicMock()
        sender_acc.username = "raha.rostamim"
        sender_acc.proxy_url = "socks5h://xray:1095"
        mock_acc_qs.filter.return_value = [sender_acc]

        # Mock active spare proxy
        spare_proxy = MagicMock()
        spare_proxy.local_port = 1088
        spare_proxy.ping_ms = 95.0

        mock_filter_chain = MagicMock()
        mock_filter_chain.exclude.return_value.order_by.return_value.first.return_value = spare_proxy
        mock_proxy_qs.filter.return_value = mock_filter_chain

        res = task_healthcheck_and_heal_proxies()
        self.assertTrue(res["success"])
        self.assertEqual(res["healed_count"], 1)
        self.assertEqual(res["details"][0]["new_port"], 1088)
        self.assertEqual(sender_acc.proxy_url, "socks5h://xray:1088")
        sender_acc.save.assert_called_once()
