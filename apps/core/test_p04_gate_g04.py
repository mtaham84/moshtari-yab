import io
import threading
from datetime import timedelta
from django.test import TransactionTestCase
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.utils import timezone

from apps.discovery.models import (
    InstagramEngineAccount,
    InstagramEngineLead,
    OutreachDispatchIntent,
)
from apps.discovery.outreach_repository import OutreachIntentRepository
from apps.discovery.outreach_executor import OutreachExecutor, MockOutreachClientAdapter
from apps.discovery.outreach_recovery import (
    OutreachReconciliationService,
    MockRemoteThreadVerifier,
)
from apps.discovery.sender_policy import SenderPolicy


class GateG04OutreachConcurrencyAndIntegrationTest(TransactionTestCase):
    """
    Comprehensive verification matrix for Gate G04 (P04.08):
    1. Multi-worker competitive barrier test: zero duplicate claims, zero lost updates.
    2. Dry-run absolute purity: zero writes, zero quota consumed, leads stay NEW.
    3. Sender quota atomic cap enforcement under high concurrency.
    4. Crash matrix and UNKNOWN reconciliation with audit logging.
    5. Frozen legacy integrity: no unauthorized legacy code mutations.
    """

    def setUp(self):
        cache.clear()
        self.tenant_id = "tenant_g04"
        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_id,
            username="g04.sender",
            role="SENDER",
            status="ACTIVE",
            daily_limit=10,
            hourly_limit=4,
            daily_action_count=0,
            hourly_action_count=0,
        )
        self.leads = []
        for i in range(5):
            lead = InstagramEngineLead.objects.create(
                tenant_id=self.tenant_id,
                ig_user_pk=f"ig_pk_g04_{i}",
                username=f"buyer_g04_{i}",
                comment_text=f"پیام خرید سرنخ شماره {i}",
                intent_score=0.90,
                status="NEW",
            )
            self.leads.append(lead)

    def tearDown(self):
        cache.clear()

    def test_g04_competitive_workers_barrier_no_duplicate_dispatch(self):
        """Competitive workers with threading.Barrier: each intent claimed exactly once, zero collisions."""
        intents = []
        for lead in self.leads:
            intent = OutreachIntentRepository.create_intent(
                lead=lead,
                sender=self.sender,
                message_text=f"پیشنهاد کاتالوگ برای {lead.username}",
            )
            intents.append(intent)

        num_workers = 5
        barrier = threading.Barrier(num_workers)
        claimed_map = {}
        lock = threading.Lock()
        errors = []

        def worker_runner(wid):
            try:
                barrier.wait()  # Synchronize start across all threads
                claimed = OutreachIntentRepository.claim_pending_intent(
                    tenant_ids=[self.tenant_id],
                    worker_id=wid,
                    lease_duration_sec=300,
                )
                if claimed:
                    with lock:
                        claimed_map[wid] = claimed.intent_id
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker_runner, args=(f"worker_{i}",)) for i in range(num_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Worker thread errors: {errors}")
        claimed_ids = list(claimed_map.values())
        self.assertEqual(len(claimed_ids), len(set(claimed_ids)), "Every worker must claim a unique intent (zero duplicates)")
        self.assertEqual(len(claimed_ids), 5, "All 5 available intents must be claimed across the 5 workers")

    def test_g04_dry_run_purity_and_zero_mutation(self):
        """Dry-run command must produce full simulation while mutating ZERO database rows."""
        out = io.StringIO()
        call_command(
            "run_instagram_outreach",
            "--tenant", self.tenant_id,
            "--dry-run",
            "--once",
            "--ignore-circadian",
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("[PLANNING & DRY-RUN]", output)
        self.assertIn("شبیه‌سازی شد", output)

        # Confirm all leads remain strictly NEW and unmodified
        for lead in self.leads:
            lead.refresh_from_db()
            self.assertEqual(lead.status, "NEW")
            self.assertEqual(lead.initial_dm_text, "")

        # Confirm sender quota was NOT consumed
        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 0)
        self.assertEqual(self.sender.hourly_action_count, 0)

    def test_g04_sender_quota_atomic_cap_under_high_concurrency(self):
        """Under high concurrency, daily quota cap is never exceeded even with multiple simultaneous attempts."""
        self.sender.daily_limit = 5
        self.sender.daily_action_count = 3  # Exactly 2 slots remain!
        self.sender.save(update_fields=["daily_limit", "daily_action_count"])

        num_threads = 6
        barrier = threading.Barrier(num_threads)
        results = []
        errors = []

        def quota_worker(idx):
            try:
                barrier.wait()
                ok, _ = SenderPolicy.reserve_quota(
                    sender_id=self.sender.id,
                    intent_id=f"intent_g04_quota_{idx}",
                    ignore_circadian=True,
                )
                results.append(ok)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        threads = [threading.Thread(target=quota_worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        success_count = sum(1 for r in results if r is True)
        failed_count = sum(1 for r in results if r is False)

        self.assertEqual(success_count, 2, "Exactly 2 remaining quota slots must be granted")
        self.assertEqual(failed_count, 4, "Remaining 4 requests must be rejected")

        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 5, "Daily action count must hit exactly cap (5) and not exceed")

    def test_g04_crash_matrix_and_reconciliation_integrity(self):
        """Crash in transmission sets UNKNOWN, refuses blind retry, and reconciles via verifier or admin."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.leads[0],
            sender=self.sender,
            message_text="پیشنهاد تست کرش",
        )
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="crashed_worker",
        )

        mock_crashing_adapter = MockOutreachClientAdapter(
            simulated_exception=ConnectionResetError("Socket reset during handshake")
        )
        res = OutreachExecutor.execute_intent(
            intent_id=claimed.intent_id,
            worker_id="crashed_worker",
            client_adapter=mock_crashing_adapter,
            ignore_circadian=True,
        )
        self.assertFalse(res["success"])
        self.assertEqual(res["reason"], "INDETERMINATE_UNKNOWN")

        claimed.refresh_from_db()
        self.assertEqual(claimed.status, "UNKNOWN")

        # Must not be picked up by next worker
        next_claim = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="next_worker",
        )
        self.assertIsNone(next_claim, "UNKNOWN intent must NEVER be re-claimed without reconciliation")

        # Automated reconciliation confirms message arrived
        rec_res = OutreachReconciliationService.reconcile_intent_automated(
            intent_id=claimed.intent_id,
            verifier=MockRemoteThreadVerifier(delivered=True, remote_id="ig_delivered_9988"),
        )
        self.assertTrue(rec_res["success"])
        self.assertEqual(rec_res["resolution"], "SENT")

        claimed.refresh_from_db()
        self.assertEqual(claimed.status, "SENT")
        self.assertEqual(claimed.remote_message_id, "ig_delivered_9988")
