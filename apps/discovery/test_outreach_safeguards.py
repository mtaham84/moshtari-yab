"""Regression tests for outreach safeguards lost in the 2026-11-05 merges (harmonize + new-insta)."""
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.discovery.anti_ban import InstagramAntiBanManager
from apps.discovery.management.commands.run_instagram_outreach import (
    PROXY_ERROR_MARKERS,
    SESSION_EXPIRED_MARKERS,
    _error_matches,
)
from apps.discovery.models import InstagramEngineAccount
from apps.discovery.outreach_service import get_effective_sender_limits


class RoleAndLimitTests(SimpleTestCase):
    def test_validate_account_role(self):
        ok, _ = InstagramAntiBanManager.validate_account_role(SimpleNamespace(role="SENDER", username="a"), "SENDER")
        self.assertTrue(ok)
        ok, msg = InstagramAntiBanManager.validate_account_role(SimpleNamespace(role="SCRAPER", username="a"), "SENDER")
        self.assertFalse(ok)
        self.assertIn("SCRAPER", msg)

    def test_new_account_is_capped_by_warmup_tier(self):
        acc = SimpleNamespace(account_tier="NEW", daily_limit=18, hourly_limit=3)
        self.assertEqual(get_effective_sender_limits(acc, fallback_daily=18), (10, 2))

    def test_aged_account_keeps_its_own_limits(self):
        acc = SimpleNamespace(account_tier="AGED", daily_limit=35, hourly_limit=5)
        self.assertEqual(get_effective_sender_limits(acc), (35, 5))

    def test_fallback_daily_is_used_when_account_has_no_limit(self):
        acc = SimpleNamespace(account_tier="MEDIUM", daily_limit=0, hourly_limit=0)
        self.assertEqual(get_effective_sender_limits(acc, fallback_daily=12), (12, 4))

    def test_error_markers(self):
        self.assertTrue(_error_matches("loginrequired: login_required", SESSION_EXPIRED_MARKERS))
        self.assertTrue(_error_matches("max retries exceeded (caused by proxyconnectionerror)", PROXY_ERROR_MARKERS))
        self.assertFalse(_error_matches("feedback_required", SESSION_EXPIRED_MARKERS + PROXY_ERROR_MARKERS))


class SingleDispatcherTests(SimpleTestCase):
    def test_beat_schedules_dm_dispatcher_only_for_celery(self):
        scheduled = "dispatch-instagram-outreach-periodic" in settings.CELERY_BEAT_SCHEDULE
        self.assertEqual(scheduled, settings.INSTAGRAM_OUTREACH_DISPATCHER == "celery")

    @override_settings(INSTAGRAM_OUTREACH_DISPATCHER="command")
    def test_celery_task_is_noop_when_command_is_dispatcher(self):
        from apps.discovery import tasks

        with patch.object(tasks, "get_available_senders_atomic", side_effect=AssertionError("must not run")):
            result = tasks.task_dispatch_instagram_outreach("tenant_4")
        self.assertTrue(result["skipped"])
        self.assertEqual(result["sent_count"], 0)

    @override_settings(INSTAGRAM_OUTREACH_DISPATCHER="celery")
    def test_command_refuses_to_send_when_celery_is_dispatcher(self):
        with self.assertRaises(CommandError):
            call_command("run_instagram_outreach", "--once")


class CelerySenderSelectionTests(TestCase):
    def test_sender_selection_runs_and_respects_role_and_tier_caps(self):
        from apps.discovery.tasks import get_available_senders_atomic

        for name in ("fresh_a", "fresh_b"):
            InstagramEngineAccount.objects.create(
                username=name, password="x", role="SENDER", tenant_id="tenant_4", account_tier="NEW", daily_action_count=3,
            )
        InstagramEngineAccount.objects.create(
            username="capped", password="x", role="SENDER", tenant_id="tenant_4", account_tier="NEW", daily_action_count=10,
        )
        InstagramEngineAccount.objects.create(username="scraper", password="x", role="SCRAPER", tenant_id="tenant_4")

        available = get_available_senders_atomic(["tenant_4"])

        self.assertCountEqual([a.username for a in available], ["fresh_a", "fresh_b"])
