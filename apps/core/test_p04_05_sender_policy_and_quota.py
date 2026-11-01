import threading
from datetime import datetime, timedelta, timezone as dt_timezone
from django.test import TransactionTestCase
from django.core.cache import cache
from django.db import connection
from django.utils import timezone
from unittest.mock import patch, MagicMock

from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
from apps.discovery.sender_policy import SenderPolicy, TEHRAN_TZ
from apps.discovery.outreach_service import send_second_followup_message


class SenderPolicyAndQuotaTestCase(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.tenant_id = "tenant_policy_p04"
        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_id,
            username="sender.policy",
            role="SENDER",
            status="ACTIVE",
            daily_limit=18,
            hourly_limit=5,
            daily_action_count=17,   # Exactly 1 capacity slot remaining!
            hourly_action_count=1,
            last_action_at=timezone.now(),
        )
        self.lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            ig_user_pk="ig_pk_9901",
            username="policy_lead",
            comment_text="تست سیاست سهمیه",
            status="NEW",
        )

    def tearDown(self):
        cache.clear()

    def test_concurrent_reservation_on_last_slot_permits_exactly_one(self):
        """Two concurrent workers competing for the last quota slot: exactly one succeeds."""
        clock = datetime(2026, 11, 1, 14, 0, tzinfo=TEHRAN_TZ)
        results = []
        errors = []

        def worker_task(intent_id):
            try:
                res, reason = SenderPolicy.reserve_quota(
                    sender_id=self.sender.id,
                    intent_id=intent_id,
                    clock=clock,
                    ignore_circadian=True,
                )
                results.append((intent_id, res, reason))
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=worker_task, args=("intent_A",))
        t2 = threading.Thread(target=worker_task, args=("intent_B",))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Errors encountered: {errors}")
        self.assertEqual(len(results), 2)

        successful = [r for r in results if r[1] is True]
        rejected = [r for r in results if r[1] is False]

        self.assertEqual(len(successful), 1, "Exactly one worker must claim the remaining quota slot")
        self.assertEqual(len(rejected), 1, "The competing worker must be rejected")
        self.assertIn("تکمیل شده است", rejected[0][2])

        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 18, "Daily limit must not exceed 18")

    def test_retry_same_intent_is_idempotent_and_does_not_double_consume(self):
        """Retrying quota reservation with the same intent_id does not consume extra units."""
        clock = datetime(2026, 11, 1, 14, 0, tzinfo=TEHRAN_TZ)
        intent_id = "retry_intent_unique_1"

        ok1, reason1 = SenderPolicy.reserve_quota(self.sender.id, intent_id, clock=clock, ignore_circadian=True)
        self.assertTrue(ok1)
        self.assertEqual(reason1, "QUOTA_RESERVED")

        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 18)

        # Retry with identical intent_id
        ok2, reason2 = SenderPolicy.reserve_quota(self.sender.id, intent_id, clock=clock, ignore_circadian=True)
        self.assertTrue(ok2)
        self.assertEqual(reason2, "QUOTA_ALREADY_RESERVED")

        # Quota remains 18 (NOT incremented to 19)
        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 18)

    def test_compensatory_release_quota_restores_capacity(self):
        """Releasing quota restores daily & hourly action counts."""
        intent_id = "cancelled_intent_1"
        ok, _ = SenderPolicy.reserve_quota(self.sender.id, intent_id, ignore_circadian=True)
        self.assertTrue(ok)

        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 18)

        # Release
        released = SenderPolicy.release_quota(self.sender.id, intent_id)
        self.assertTrue(released)

        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 17)

    def test_followup_message_strictly_enforces_sender_quota_and_circadian(self):
        """send_followup_message rejects dispatch when quota is exhausted (R12)."""
        # Exhaust quota
        self.sender.daily_action_count = 18
        self.sender.session_json = {"cookies": {"sessionid": "valid_sess"}}
        self.sender.save(update_fields=["daily_action_count", "session_json"])

        res = send_second_followup_message(
            lead_id=self.lead.id,
            sender_username=self.sender.username,
            message_text="پیام پیگیری تست",
            actor_user_or_tenant=self.tenant_id,
        )

        self.assertFalse(res["success"])
        self.assertIn("محدودیت سهمیه", res["error"])

    def test_admin_override_quota_with_audit_logging(self):
        """Admin override updates limit and writes to security.audit logger."""
        with self.assertLogs("security.audit", level="WARNING") as cm:
            SenderPolicy.admin_override_quota(
                sender_id=self.sender.id,
                new_daily_limit=25,
                reason="تأییدیه مدیریت برای کمپین فصلی",
                admin_username="admin_super",
            )
            self.assertTrue(any("SENDER_QUOTA_ADMIN_OVERRIDE" in log for log in cm.output))

        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_limit, 25)

        # Reject empty reason
        with self.assertRaises(ValueError):
            SenderPolicy.admin_override_quota(
                sender_id=self.sender.id,
                new_daily_limit=30,
                reason="",
                admin_username="admin_super",
            )
