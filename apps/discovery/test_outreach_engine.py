"""The single DM dispatcher shared by ig-outreach and Celery (apps.discovery.outreach_engine)."""
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.discovery import outreach_engine as engine
from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead

TENANT = "tenant_9"


@override_settings(INSTAGRAM_OUTREACH_MIN_GAP_MINUTES=0)
class OutreachEngineTests(TestCase):
    def setUp(self):
        self.ig = MagicMock()
        engine._CLIENTS.clear()
        self.addCleanup(engine._CLIENTS.clear)
        for target in (
            patch.object(engine, "get_sender_client", side_effect=self._client_for),
            patch("apps.discovery.proxy_service.prepare_sender_proxy_before_dm", return_value={}),
            patch.object(engine, "qualify_recipient_profile", return_value={"eligible": True, "target_pk": 111, "reason": ""}),
            patch.object(engine, "execute_organic_journey", return_value={"target_pk": 111, "actions": {}}),
            patch("apps.discovery.outreach_service.execute_organic_journey", return_value={"target_pk": 111, "actions": {}}),
        ):
            target.start()
            self.addCleanup(target.stop)
        self.s1 = self._sender("s_one")

    def _client_for(self, account):
        engine._CLIENTS[account.username] = self.ig
        return self.ig

    def _sender(self, name, **extra):
        return InstagramEngineAccount.objects.create(
            username=name, password="x", role="SENDER", status="ACTIVE", tenant_id=TENANT, account_tier="AGED",
            daily_limit=30, hourly_limit=5, session_json={"cookies": {"sessionid": f"sid-{name}"}}, **extra)

    def _lead(self, name, pk, text="قیمت کامپوزیت چنده؟", comment_created_at=None, **extra):
        return InstagramEngineLead.objects.create(
            tenant_id=TENANT, username=name, ig_user_pk=pk, status="NEW", comment_text=text, is_private=False,
            comment_created_at=comment_created_at or timezone.now(), **extra)

    def _queued_lead(self, sender_name, **extra):
        return InstagramEngineLead.objects.create(
            tenant_id=TENANT, username="chatting", ig_user_pk="222", status="QUEUED", has_replied=True,
            conversation_history=[{"sender": sender_name, "direction": "out", "role": "operator_message",
                                   "text": "پاسخ اپراتور", "delivery_status": "QUEUED"}], **extra)

    def step(self, **kwargs):
        return engine.run_outreach_step([TENANT], ignore_shift=True, **kwargs)

    def test_cold_dm_paces_the_sender_instead_of_sleeping(self):
        self._lead("best", "101")
        self._lead("second", "102", text="ممنون")

        first = self.step()
        second = self.step()

        self.assertEqual(first["action"], "cold_sent")
        self.assertEqual(first["lead"], "best")
        self.s1.refresh_from_db()
        self.assertGreater(self.s1.next_outreach_at, timezone.now() + timedelta(minutes=5))
        self.assertEqual((second["action"], second["reason"]), ("waiting", "sender_pacing"))
        self.ig.direct_send.assert_called_once()
        self.assertEqual(InstagramEngineLead.objects.get(username="second").status, "NEW")

    def test_senders_send_concurrently_without_global_gap_lock(self):
        self._sender("s_two")
        self._lead("a", "101")
        self._lead("b", "102")

        first = self.step(min_gap_minutes=20)
        second = self.step(min_gap_minutes=20)

        self.assertEqual(first["action"], "cold_sent")
        self.assertEqual(first["sender"], "s_one")
        self.assertEqual(second["action"], "cold_sent")
        self.assertEqual(second["sender"], "s_two")
        self.assertEqual(self.ig.direct_send.call_count, 2)

        third = self.step(min_gap_minutes=20)
        self.assertEqual((third["action"], third["reason"]), ("waiting", "sender_pacing"))

    def test_never_used_senders_are_picked_oldest_account_first(self):
        # The sender query has no ORDER BY, so PostgreSQL may return the rows in any order (it depends on the query
        # plan and table statistics). Return them newest first, as such a plan would; the pick must not change.
        self._sender("s_two")
        real_filter = InstagramEngineAccount.objects.filter
        with patch.object(InstagramEngineAccount.objects, "filter",
                          lambda *a, **kw: real_filter(*a, **kw).order_by("-pk")):
            picked = engine.get_available_senders([TENANT], refresh=False)
        self.assertEqual([s.username for s in picked], ["s_one", "s_two"])

    def test_rate_limited_sender_rests_and_another_sender_takes_over(self):
        self._sender("s_two", last_action_at=timezone.now() - timedelta(hours=1))
        self.s1.last_action_at = timezone.now() - timedelta(hours=2)  # least recently used -> picked first
        self.s1.save(update_fields=["last_action_at"])
        self._lead("a", "101")
        self.ig.direct_send.side_effect = [Exception("feedback_required: please wait a few minutes"), None]

        failed = self.step()
        self.s1.refresh_from_db()
        self.assertEqual((failed["action"], failed["kind"], failed["sender"]), ("cold_failed", "rate_limit", "s_one"))
        self.assertEqual(self.s1.status, "COOLDOWN")
        self.assertGreater(self.s1.cooldown_until, timezone.now() + timedelta(hours=2))
        self.assertEqual(InstagramEngineLead.objects.get(username="a").status, "NEW")

        retried = self.step()
        self.assertEqual((retried["action"], retried["sender"]), ("cold_sent", "s_two"))

    def test_queued_message_waits_for_a_usable_sender_and_goes_first(self):
        self.s1.status = "CHALLENGED"
        self.s1.save(update_fields=["status"])
        queued = self._queued_lead("s_one")

        self.assertNotEqual(self.step()["action"], "queued_sent")
        queued.refresh_from_db()
        self.assertEqual(queued.status, "QUEUED")

        self.s1.status = "ACTIVE"
        self.s1.save(update_fields=["status"])
        self._lead("cold", "101")
        sent = self.step()

        self.assertEqual((sent["action"], sent["sender"]), ("queued_sent", "s_one"))
        queued.refresh_from_db()
        self.assertEqual(queued.status, "FOLLOWUP_SENT")
        self.assertEqual(InstagramEngineLead.objects.get(username="cold").status, "NEW")

    def test_failed_queued_message_backs_off_then_gives_up(self):
        queued = self._queued_lead("s_one")
        self.ig.direct_send.side_effect = Exception("unexpected server answer")

        self.assertEqual(self.step()["action"], "queued_failed")
        queued.refresh_from_db()
        item = queued.conversation_history[0]
        self.assertEqual((queued.status, item["delivery_status"], item["attempts"]), ("QUEUED", "QUEUED", 1))
        self.assertIn("retry_after", item)
        self.assertNotEqual(self.step()["action"], "queued_failed")  # backing off

        item.pop("retry_after")
        item["attempts"] = engine.QUEUED_MAX_ATTEMPTS - 1
        queued.conversation_history = [item]
        queued.save(update_fields=["conversation_history"])
        self.s1.last_action_at = None
        self.s1.save(update_fields=["last_action_at"])
        self.assertEqual(self.step()["action"], "queued_failed")
        queued.refresh_from_db()
        self.assertEqual(queued.conversation_history[0]["delivery_status"], "FAILED")
        self.assertEqual(queued.status, "REPLIED")

    def test_skip_locked_lead_reservation(self):
        """Verify select_and_reserve_candidate marks candidate as PROCESSING so subsequent calls pick next lead."""
        self._lead("lead_1", "101")
        self._lead("lead_2", "102")

        cand1 = engine.select_and_reserve_candidate([TENANT])
        self.assertIsNotNone(cand1)
        self.assertEqual(cand1.status, "PROCESSING")

        cand2 = engine.select_and_reserve_candidate([TENANT])
        self.assertIsNotNone(cand2)
        self.assertEqual(cand2.status, "PROCESSING")
        self.assertNotEqual(cand1.id, cand2.id)

        cand3 = engine.select_and_reserve_candidate([TENANT])
        self.assertIsNone(cand3)

    def test_fifteen_day_old_lead_not_skipped_and_scores_lower_than_fresh(self):
        now = timezone.now()
        fresh = self._lead("fresh_user", "201", comment_created_at=now - timedelta(hours=2))
        older = self._lead("older_user", "202", comment_created_at=now - timedelta(days=15))

        first = engine.select_and_reserve_candidate([TENANT], now=now)
        self.assertIsNotNone(first)
        self.assertEqual(first.id, fresh.id)

        older.refresh_from_db()
        self.assertNotEqual(older.status, "SKIPPED")
        self.assertEqual(older.status, "NEW")
        self.assertLess(older.outreach_score, first.outreach_score)

        second = engine.select_and_reserve_candidate([TENANT], now=now)
        self.assertIsNotNone(second)
        self.assertEqual(second.id, older.id)
        self.assertEqual(second.status, "PROCESSING")

    def test_shift_pause_sends_nothing(self):
        self._lead("a", "101")
        with patch.object(engine.HumanShiftPlanner, "is_shift_active", return_value=(False, "استراحت شبانه", 600)):
            result = engine.run_outreach_step([TENANT])
        self.assertEqual(result["action"], "paused")
        self.ig.direct_send.assert_not_called()


class OrganicSendPathTests(TestCase):
    def test_send_path_works_without_heavy_organic_steps(self):
        """Verify pre-send journey only resolves PK and applies typing delay without heavy browsing."""
        from apps.discovery.outreach_service import execute_organic_journey
        cl = MagicMock()
        cl.user_id_from_username.return_value = 777

        with patch("apps.discovery.outreach_service.time.sleep"):
            res = execute_organic_journey(cl, "target_buyer", target_pk=None)

        self.assertEqual(res["target_pk"], 777)
        self.assertIn("typing_delay_sec", res["actions"])
        self.assertGreaterEqual(res["actions"]["typing_delay_sec"], 6.0)
        self.assertLessEqual(res["actions"]["typing_delay_sec"], 10.0)

        # Ensure no heavy feed or media calls were made on the Instagram client
        cl.get_timeline_feed.assert_not_called()
        cl.user_medias.assert_not_called()
        cl.user_stories.assert_not_called()
        cl.media_like.assert_not_called()


class SenderClientCacheTests(TestCase):
    def test_client_is_reused_until_session_changes(self):
        engine._CLIENTS.clear()
        self.addCleanup(engine._CLIENTS.clear)
        acc = InstagramEngineAccount.objects.create(
            username="cached", password="x", role="SENDER", tenant_id=TENANT, proxy_url="socks5h://xray:1095#rotate=http://r",
            session_json={"cookies": {"sessionid": "one"}})
        with patch("instagrapi.Client") as client_cls, patch.object(engine, "connect_saved_session"):
            first = engine.get_sender_client(acc)
            self.assertIs(engine.get_sender_client(acc), first)
            first.set_proxy.assert_called_once_with("socks5h://xray:1095")  # rotation fragment stripped
            acc.session_json = {"cookies": {"sessionid": "two"}}
            engine.get_sender_client(acc)
        self.assertEqual(client_cls.call_count, 2)
