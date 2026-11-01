import threading
from datetime import timedelta
from django.test import TransactionTestCase
from django.db import connection
from django.utils import timezone

from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead, OutreachDispatchIntent
from apps.discovery.outreach_repository import OutreachIntentRepository


class AtomicClaimAndLeaseTestCase(TransactionTestCase):
    def setUp(self):
        self.tenant_id = "tenant_claim_p04"
        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_id,
            username="claim.sender",
            role="SENDER",
            status="ACTIVE",
        )
        self.lead1 = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            ig_user_pk="ig_pk_101",
            username="claim_lead_1",
            comment_text="تست claim اول",
            status="NEW",
        )
        self.lead2 = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            ig_user_pk="ig_pk_102",
            username="claim_lead_2",
            comment_text="تست claim دوم",
            status="NEW",
        )

    def test_concurrent_workers_claim_distinct_intents_without_collision(self):
        """Two concurrent workers competing for 1 intent: exactly one claims it, the other gets None."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead1,
            sender=self.sender,
            message_text="پیام تست همزمانی claim",
        )

        results = []
        errors = []

        def worker_task(wid):
            try:
                claimed = OutreachIntentRepository.claim_pending_intent(
                    tenant_ids=[self.tenant_id],
                    worker_id=wid,
                    lease_duration_sec=300,
                )
                results.append((wid, claimed))
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        t1 = threading.Thread(target=worker_task, args=("worker_A",))
        t2 = threading.Thread(target=worker_task, args=("worker_B",))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Errors occurred: {errors}")
        self.assertEqual(len(results), 2)

        claimed_intents = [res for wid, res in results if res is not None]
        none_results = [res for wid, res in results if res is None]

        self.assertEqual(len(claimed_intents), 1, "Exactly one worker must claim the single intent")
        self.assertEqual(len(none_results), 1, "The competing worker must receive None (no double claim)")

        intent.refresh_from_db()
        self.assertEqual(intent.status, "CLAIMED")
        self.assertIn(intent.worker_id, ["worker_A", "worker_B"])
        self.assertEqual(intent.generation, 2)

    def test_cas_update_succeeds_for_owner_and_fails_for_stale_worker(self):
        """Compare-and-set succeeds for active owner with valid generation and fails for stale workers."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead1,
            sender=self.sender,
            message_text="تست CAS",
        )
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_active",
            lease_duration_sec=300,
        )
        self.assertIsNotNone(claimed)
        gen_after_claim = claimed.generation  # Should be 2

        # 1. Stale worker with wrong worker_id attempts CAS -> must fail (returns False)
        cas_stale_worker = OutreachIntentRepository.compare_and_set_status(
            intent_id=claimed.intent_id,
            expected_generation=gen_after_claim,
            expected_worker_id="worker_impostor",
            new_status="SENDING",
        )
        self.assertFalse(cas_stale_worker, "Stale worker with wrong worker_id must fail CAS")

        # 2. Worker with wrong generation attempts CAS -> must fail
        cas_stale_gen = OutreachIntentRepository.compare_and_set_status(
            intent_id=claimed.intent_id,
            expected_generation=gen_after_claim - 1,
            expected_worker_id="worker_active",
            new_status="SENDING",
        )
        self.assertFalse(cas_stale_gen, "Worker with stale generation must fail CAS")

        # 3. Legitimate worker with matching generation and worker_id -> succeeds
        cas_legit = OutreachIntentRepository.compare_and_set_status(
            intent_id=claimed.intent_id,
            expected_generation=gen_after_claim,
            expected_worker_id="worker_active",
            new_status="SENDING",
            new_worker_id="worker_active",
            increment_attempt=True,
        )
        self.assertTrue(cas_legit, "Legitimate owner with valid generation must succeed CAS")

        claimed.refresh_from_db()
        self.assertEqual(claimed.status, "SENDING")
        self.assertEqual(claimed.attempt_count, 1)
        self.assertEqual(claimed.generation, gen_after_claim + 1)

    def test_reclaim_expired_leases_safely_differentiates_claimed_and_sending(self):
        """
        Expired CLAIMED returns to PENDING.
        Expired SENDING MUST transition to UNKNOWN (never PENDING) to prevent duplicate dispatch.
        """
        now = timezone.now()
        past = now - timedelta(minutes=10)

        # 1. Expired CLAIMED intent (never sent to network)
        intent_claimed = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead1,
            message_type="initial",
            status="CLAIMED",
            worker_id="worker_crashed_early",
            lease_expires_at=past,
            generation=2,
        )

        # 2. Expired SENDING intent (transmission was in flight when worker crashed)
        intent_sending = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead2,
            message_type="initial",
            status="SENDING",
            worker_id="worker_crashed_inflight",
            lease_expires_at=past,
            generation=3,
        )

        # 3. Active (unexpired) CLAIMED intent
        intent_active = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead1,
            message_type="followup",
            status="CLAIMED",
            worker_id="worker_live",
            lease_expires_at=now + timedelta(minutes=5),
            generation=2,
        )

        reclaim_res = OutreachIntentRepository.reclaim_expired_leases(now=now)
        self.assertEqual(reclaim_res["reclaimed_to_pending"], 1)
        self.assertEqual(reclaim_res["transitioned_to_unknown"], 1)

        # Verify intent 1 returned to PENDING safely
        intent_claimed.refresh_from_db()
        self.assertEqual(intent_claimed.status, "PENDING")
        self.assertIsNone(intent_claimed.worker_id)
        self.assertIsNone(intent_claimed.lease_expires_at)
        self.assertEqual(intent_claimed.generation, 3)

        # Verify intent 2 transitioned to UNKNOWN (NOT PENDING!)
        intent_sending.refresh_from_db()
        self.assertEqual(intent_sending.status, "UNKNOWN")
        self.assertIsNone(intent_sending.worker_id)
        self.assertEqual(intent_sending.error_code, "TIMEOUT_CRASH")
        self.assertEqual(intent_sending.generation, 4)

        # Verify active intent was untouched
        intent_active.refresh_from_db()
        self.assertEqual(intent_active.status, "CLAIMED")
        self.assertEqual(intent_active.worker_id, "worker_live")
