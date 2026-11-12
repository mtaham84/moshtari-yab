"""Tests that PROCESSING lead reservations expire and return to their previous status (Roadmap 7.3 / B-8)."""
from datetime import datetime, timedelta, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.discovery import outreach_engine as engine
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
from apps.discovery.outreach import recipients


class ProcessingReservationUnitTests(SimpleTestCase):
    """Fast unit tests (no DB required) for reserved_at assignment and stale reservation release."""

    def _lead_stub(self, **kwargs):
        defaults = {
            "pk": 1,
            "tenant_id": "tenant_7",
            "username": "buyer",
            "ig_user_pk": "101",
            "status": "NEW",
            "reserved_at": None,
            "outreach_score": 20,
            "comment_created_at": datetime(2026, 11, 10, 10, 0, tzinfo=dt_timezone.utc),
            "created_at": datetime(2026, 11, 10, 10, 0, tzinfo=dt_timezone.utc),
            "updated_at": datetime(2026, 11, 10, 10, 0, tzinfo=dt_timezone.utc),
            "conversation_history": [],
            "has_replied": False,
            "initial_dm_at": None,
            "comment_text": "قیمت چنده؟",
            "is_private": False,
        }
        defaults.update(kwargs)
        saved_calls = []

        def _save(update_fields=None):
            saved_calls.append(list(update_fields or []))

        obj = SimpleNamespace(save=_save, _saved=saved_calls, **defaults)
        return obj

    def test_select_and_reserve_candidate_sets_reserved_at(self):
        now = datetime(2026, 11, 10, 12, 0, tzinfo=dt_timezone.utc)
        cand = self._lead_stub(status="NEW")

        qs = MagicMock()
        qs.filter.return_value = qs
        qs.order_by.return_value = [cand]

        with patch.object(InstagramEngineLead.objects, "select_for_update", return_value=qs), \
             patch("django.db.transaction.atomic", side_effect=lambda: MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False)), \
             patch("apps.discovery.instagram_scoring.get_outreach_cutoff_days", return_value=30), \
             patch("apps.discovery.instagram_scoring.update_lead_scoring", return_value={"total_score": 25, "is_eligible": True}):
            winner = recipients.select_and_reserve_candidate(["tenant_7"], now=now)

        self.assertIs(winner, cand)
        self.assertEqual(winner.status, "PROCESSING")
        self.assertEqual(winner.reserved_at, now)
        self.assertIn("reserved_at", winner._saved[-1])

    def test_send_next_queued_sets_reserved_at(self):
        now = datetime(2026, 11, 10, 12, 0, tzinfo=dt_timezone.utc)
        cand = self._lead_stub(
            status="QUEUED",
            conversation_history=[{
                "sender": "s1",
                "direction": "out",
                "role": "operator_message",
                "text": "سلام",
                "delivery_status": "QUEUED",
            }],
        )
        sender = SimpleNamespace(
            pk=5,
            username="s1",
            role="SENDER",
            status="ACTIVE",
            cooldown_until=None,
            last_action_at=None,
        )
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.order_by.return_value = [cand]
        ctx = engine._Ctx(
            ["tenant_7"], dry_run=True, max_daily_dms=10, min_gap_minutes=0,
            log=lambda *a: None, stdout=None, cfg={},
        )

        with patch.object(InstagramEngineLead.objects, "select_for_update", return_value=qs), \
             patch("apps.discovery.outreach_engine.transaction.atomic", side_effect=lambda: MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False)), \
             patch("apps.discovery.outreach_engine.resolve_queued_sender", return_value=sender), \
             patch("apps.discovery.outreach_engine.process_queued_outbox_message", return_value={"success": True}):
            res = engine._send_next_queued(ctx, now)

        self.assertEqual(res["action"], "queued_sent")
        self.assertIn("reserved_at", cand._saved[0])

    def test_release_stale_reservations_restores_previous_statuses(self):
        now = datetime(2026, 11, 10, 12, 30, tzinfo=dt_timezone.utc)
        old = now - timedelta(minutes=15)

        stale_cold = self._lead_stub(pk=1, status="PROCESSING", reserved_at=old)
        stale_queued = self._lead_stub(
            pk=2,
            status="PROCESSING",
            reserved_at=old,
            conversation_history=[{"direction": "out", "delivery_status": "QUEUED", "text": "پاسخ"}],
        )
        stale_replied = self._lead_stub(pk=3, status="PROCESSING", reserved_at=old, has_replied=True)
        stale_legacy = self._lead_stub(pk=4, status="PROCESSING", reserved_at=None, updated_at=old)

        qs = MagicMock()
        qs.filter.return_value = qs
        qs.__iter__.return_value = iter([stale_cold, stale_queued, stale_replied, stale_legacy])

        with patch.object(InstagramEngineLead.objects, "select_for_update", return_value=qs), \
             patch("django.db.transaction.atomic", side_effect=lambda: MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False)):
            released = recipients.release_stale_reservations(["tenant_7"], now=now)

        self.assertEqual(released, 4)
        self.assertEqual((stale_cold.status, stale_cold.reserved_at), ("NEW", None))
        self.assertEqual((stale_queued.status, stale_queued.reserved_at), ("QUEUED", None))
        self.assertEqual((stale_replied.status, stale_replied.reserved_at), ("REPLIED", None))
        self.assertEqual((stale_legacy.status, stale_legacy.reserved_at), ("NEW", None))

    def test_run_outreach_step_releases_stale_reservations_before_picking(self):
        with patch.object(engine.HumanShiftPlanner, "is_shift_active", return_value=(True, "", 0)), \
             patch("apps.discovery.outreach_engine.refresh_sender_states"), \
             patch("apps.discovery.outreach_engine.release_stale_reservations", return_value=1) as release, \
             patch("apps.discovery.outreach_engine._send_next_queued", return_value=None), \
             patch("apps.discovery.outreach_engine._send_next_cold", return_value={"action": "idle", "sent": False}):
            res = engine.run_outreach_step(["tenant_7"])

        self.assertEqual(res["action"], "idle")
        release.assert_called_once()

    def test_completing_or_retrying_clears_reserved_at(self):
        now = datetime(2026, 11, 10, 12, 0, tzinfo=dt_timezone.utc)
        lead = self._lead_stub(status="PROCESSING", reserved_at=now)
        sender = SimpleNamespace(
            username="s1",
            daily_action_count=0,
            hourly_action_count=0,
            account_tier="AGED",
            last_action_at=None,
            status_message="",
            next_outreach_at=None,
            save=lambda update_fields=None: None,
        )
        with patch("apps.discovery.outreach_engine.transaction.atomic", side_effect=lambda: MagicMock(__enter__=lambda s: None, __exit__=lambda s, *a: False)):
            engine._mark_cold_sent(lead, sender, "سلام", pace=False)
        self.assertEqual(lead.status, "DM_SENT")
        self.assertIsNone(lead.reserved_at)
        self.assertIn("reserved_at", lead._saved[-1])


@override_settings(INSTAGRAM_OUTREACH_MIN_GAP_MINUTES=0)
class ProcessingReservationIntegrationTests(TestCase):
    """Full PostgreSQL integration tests verifying stuck PROCESSING leads are picked up again."""

    def setUp(self):
        self.tenant = "tenant_19"
        self.sender = InstagramEngineAccount.objects.create(
            username="proc_sender",
            password="x",
            role="SENDER",
            status="ACTIVE",
            tenant_id=self.tenant,
            account_tier="AGED",
            daily_limit=30,
            hourly_limit=5,
            session_json={"cookies": {"sessionid": "sid-proc"}},
        )

    def test_stale_processing_cold_lead_is_released_and_picked_up_again(self):
        old = timezone.now() - timedelta(minutes=recipients.PROCESSING_TIMEOUT_MINUTES + 5)
        stuck = InstagramEngineLead.objects.create(
            tenant_id=self.tenant,
            username="stuck_cold",
            ig_user_pk="801",
            status="PROCESSING",
            reserved_at=old,
            comment_text="قیمت چنده؟",
            is_private=False,
            comment_created_at=timezone.now(),
        )
        fresh = InstagramEngineLead.objects.create(
            tenant_id=self.tenant,
            username="fresh_processing",
            ig_user_pk="802",
            status="PROCESSING",
            reserved_at=timezone.now() - timedelta(minutes=1),
            comment_text="قیمت چنده؟",
            is_private=False,
            comment_created_at=timezone.now(),
        )

        ig = MagicMock()
        with patch.object(engine, "get_sender_client", return_value=ig), \
             patch("apps.discovery.proxies.rotation.prepare_sender_proxy_before_dm", return_value={}), \
             patch.object(engine, "qualify_recipient_profile", return_value={"eligible": True, "target_pk": 801, "reason": ""}), \
             patch.object(engine, "execute_organic_journey", return_value={"target_pk": 801, "actions": {}}):
            res = engine.run_outreach_step([self.tenant], ignore_shift=True)

        self.assertEqual(res["action"], "cold_sent")
        self.assertEqual(res["lead"], "stuck_cold")
        stuck.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(stuck.status, "DM_SENT")
        self.assertIsNone(stuck.reserved_at)
        self.assertEqual(fresh.status, "PROCESSING")

    def test_stale_processing_queued_lead_returns_to_queued_and_sends(self):
        old = timezone.now() - timedelta(minutes=recipients.PROCESSING_TIMEOUT_MINUTES + 5)
        stuck_q = InstagramEngineLead.objects.create(
            tenant_id=self.tenant,
            username="stuck_queued",
            ig_user_pk="803",
            status="PROCESSING",
            reserved_at=old,
            has_replied=True,
            conversation_history=[{
                "sender": "proc_sender",
                "direction": "out",
                "role": "operator_message",
                "text": "پاسخ اپراتور",
                "delivery_status": "QUEUED",
            }],
        )

        res = engine.run_outreach_step([self.tenant], dry_run=True, ignore_shift=True)
        self.assertEqual(res["action"], "queued_sent")
        self.assertEqual(res["lead"], "stuck_queued")
        stuck_q.refresh_from_db()
        self.assertEqual(stuck_q.status, "FOLLOWUP_SENT")
        self.assertIsNone(stuck_q.reserved_at)
