"""
Unit tests for Celery distributed tasks, Redis task routing,
and automated proxy self-healing.
"""
from unittest.mock import patch
from django.test import SimpleTestCase, TestCase, override_settings

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

    @override_settings(INSTAGRAM_OUTREACH_DISPATCHER="celery")
    @patch("apps.discovery.tasks.HumanShiftPlanner.is_shift_active")
    def test_task_dispatch_outreach_respects_shift_pause(self, mock_shift):
        """Task should return paused without sending DMs when shift is closed."""
        mock_shift.return_value = (False, "استراحت شبانه", 3600)
        res = task_dispatch_instagram_outreach(tenant="tenant_4", ignore_shift=False)
        self.assertTrue(res["success"])
        self.assertTrue(res["paused"])
        self.assertEqual(res["sent_count"], 0)
        self.assertIn("استراحت شبانه", res["reason"])

    @patch("apps.discovery.tasks.random.random", return_value=0.9)
    @patch("apps.discovery.tasks.reply_sync_allowed", return_value=(True, ""))
    @patch("apps.discovery.tasks.check_and_sync_lead_replies")
    def test_task_sync_lead_replies_executes(self, mock_sync, _shift, _rand):
        """Verify reply polling task calls underlying sync engine."""
        mock_sync.return_value = {"success": True, "replies_found": 3, "details": []}
        res = task_sync_lead_replies(tenant_ids=["tenant_4"])
        self.assertTrue(res["success"])
        self.assertEqual(res["replies_found"], 3)
        mock_sync.assert_called_once_with(["tenant_4"])

    @patch("apps.discovery.tasks.reply_sync_allowed", return_value=(False, "استراحت شبانه"))
    @patch("apps.discovery.tasks.check_and_sync_lead_replies")
    def test_task_sync_lead_replies_sleeps_outside_shift(self, mock_sync, _shift):
        res = task_sync_lead_replies(tenant_ids=["tenant_4"])
        self.assertTrue(res["skipped"])
        mock_sync.assert_not_called()

    @patch("apps.discovery.tasks.random.random", return_value=0.1)
    @patch("apps.discovery.tasks.reply_sync_allowed", return_value=(True, ""))
    @patch("apps.discovery.tasks.check_and_sync_lead_replies")
    def test_task_sync_lead_replies_skips_some_ticks(self, mock_sync, _shift, _rand):
        res = task_sync_lead_replies(tenant_ids=["tenant_4"])
        self.assertEqual(res["reason"], "random_skip")
        mock_sync.assert_not_called()

    def test_reply_sync_beat_is_not_a_two_minute_poll(self):
        from django.conf import settings
        entry = settings.CELERY_BEAT_SCHEDULE.get("sync-lead-replies-fast")
        if entry:  # only scheduled with INSTAGRAM_OUTREACH_DISPATCHER=celery
            self.assertGreaterEqual(entry["schedule"], 300)

    @patch("apps.discovery.channel_config_automation.sync_all_active_channels")
    def test_task_sync_telegram_channels(self, mock_channels):
        """Verify channel config crawler task runs."""
        mock_channels.return_value = {"success": True, "total_channels": 2, "total_added": 4, "message": "OK"}
        res = task_sync_telegram_channel_configs(max_posts=10, max_add=5)
        self.assertTrue(res["success"])
        self.assertEqual(res["total_added"], 4)


class ProxyHealTaskTestCase(TestCase):
    """The heal task keeps working configs with their accounts and replaces only dead ones (ig_accounts)."""

    def test_task_healthcheck_heals_dead_proxy(self):
        from apps.discovery.models import InstagramAccountEvent, InstagramEngineAccount, ProxyConfig

        dead = ProxyConfig.objects.create(raw_url="vless://dead@1.1.1.1:443", protocol="vless", local_port=1095,
                                          status="ACTIVE", ping_ms=80)
        spare = ProxyConfig.objects.create(raw_url="vless://spare@2.2.2.2:443", protocol="vless", local_port=1088,
                                           status="ACTIVE", ping_ms=95)
        kept = ProxyConfig.objects.create(raw_url="vless://kept@3.3.3.3:443", protocol="vless", local_port=1090,
                                          status="ACTIVE", ping_ms=60)
        sender = InstagramEngineAccount.objects.create(username="raha.rostamim", password="x", role="SENDER",
                                                       proxy_url="socks5h://xray:1095", proxy_config=dead)
        steady = InstagramEngineAccount.objects.create(username="steady.one", password="x", role="SENDER",
                                                       proxy_url="socks5h://xray:1090", proxy_config=kept)
        own = InstagramEngineAccount.objects.create(username="own.proxy", password="x", role="SENDER",
                                                    proxy_url="http://u:p@5.5.5.5:8000")
        live_ports = {1080, 1088, 1090}   # 1095 does not answer any more
        with patch("apps.discovery.ig_accounts.port_open", side_effect=lambda host, port, timeout=1.5: port in live_ports):
            res = task_healthcheck_and_heal_proxies()

        self.assertTrue(res["success"])
        self.assertEqual(res["healed_count"], 1)
        self.assertEqual(res["details"][0]["new_port"], 1088)
        sender.refresh_from_db(); steady.refresh_from_db(); own.refresh_from_db()
        self.assertEqual((sender.proxy_config_id, sender.proxy_url), (spare.pk, "socks5h://xray:1088"))
        self.assertEqual((steady.proxy_config_id, steady.proxy_url), (kept.pk, "socks5h://xray:1090"))
        self.assertEqual(own.proxy_url, "http://u:p@5.5.5.5:8000")
        self.assertTrue(InstagramAccountEvent.objects.filter(account=sender, kind="proxy_changed").exists())
        self.assertFalse(InstagramAccountEvent.objects.filter(account=steady).exists())

    def test_task_does_nothing_while_xray_is_down(self):
        with patch("apps.discovery.ig_accounts.port_open", return_value=False):
            res = task_healthcheck_and_heal_proxies()
        self.assertEqual((res["success"], res.get("skipped")), (False, "xray_down"))
