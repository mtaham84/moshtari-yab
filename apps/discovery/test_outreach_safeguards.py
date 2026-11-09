"""Regression tests for outreach safeguards lost in the 2026-11-05 merges (harmonize + new-insta)."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.discovery.anti_ban import InstagramAntiBanManager
from apps.discovery.management.commands.run_instagram_outreach import (
    PROXY_ERROR_MARKERS,
    SESSION_EXPIRED_MARKERS,
    _error_matches,
)
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
from apps.discovery.outreach_service import get_effective_sender_limits, select_and_reserve_candidate


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

    def test_reply_polling_belongs_to_the_selected_dispatcher_only(self):
        scheduled = "sync-lead-replies-fast" in settings.CELERY_BEAT_SCHEDULE
        self.assertEqual(scheduled, settings.INSTAGRAM_OUTREACH_DISPATCHER == "celery")

    @override_settings(INSTAGRAM_OUTREACH_DISPATCHER="celery")
    def test_celery_task_delegates_to_the_shared_engine(self):
        from apps.discovery import tasks

        batch = {"sent": 1, "last": {"action": "cold_sent", "sent": True}, "steps": []}
        with patch.object(tasks, "run_outreach_batch", return_value=batch) as run:
            result = tasks.task_dispatch_instagram_outreach("tenant_4", batch_size=2, ignore_shift=True)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], ["tenant_4", "sarv"])
        self.assertEqual(run.call_args.kwargs["max_sends"], 2)
        self.assertTrue(run.call_args.kwargs["ignore_shift"])
        self.assertEqual(result["sent_count"], 1)

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


class CandidateSelectionTests(TestCase):
    def _lead(self, username, *, hours_ago, is_private, text, intent):
        return InstagramEngineLead.objects.create(
            tenant_id="tenant_4", username=username, ig_user_pk=username, status="NEW", intent_score=intent,
            comment_text=text, is_private=is_private, comment_created_at=timezone.now() - timedelta(hours=hours_ago),
        )

    def test_selection_ranks_by_45_point_score_within_30_days(self):
        self._lead("hot", hours_ago=2, is_private=False, text="قیمت کامپوزیت چنده؟", intent=0.3)
        self._lead("cold", hours_ago=60, is_private=True, text="عالیه", intent=0.99)
        self._lead("week_old", hours_ago=24 * 6, is_private=False, text="قیمت لمینت؟", intent=0.99)
        self._lead("too_old", hours_ago=24 * 32, is_private=False, text="قیمت ایمپلنت؟", intent=0.99)

        picked = [select_and_reserve_candidate(["tenant_4"]) for _ in range(4)]   # each call reserves the best one

        self.assertIsNone(picked.pop())
        self.assertEqual([lead.username for lead in picked], ["hot", "week_old", "cold"])
        self.assertGreater(picked[0].outreach_score, picked[1].outreach_score)
        self.assertGreater(picked[1].outreach_score, picked[2].outreach_score)
        self.assertTrue(picked[0].score_breakdown["service_score"])
