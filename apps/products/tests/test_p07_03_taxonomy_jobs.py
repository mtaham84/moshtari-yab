from datetime import timedelta
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.businesses.models import Business
from apps.products.models import TaxonomyJob
from apps.products.taxonomy_jobs import TaxonomyJobRepository

User = get_user_model()


class TaxonomyJobsTestCase(TestCase):
    """
    P07.03 Verification: Durable job repository for taxonomy organisation,
    lease-based claiming, crash recovery, and poison pill dead-letter protection (R24).
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="tax_user", email="tax@test.local")
        self.biz = Business.objects.create(name="Tax Business", user=self.user)

    def test_concurrent_enqueue_collapses_into_single_pending_job(self):
        """Multiple enqueues for the same business and catalog_version collapse into ONE job."""
        job1, created1 = TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=1)
        self.assertTrue(created1)
        self.assertEqual(job1.status, TaxonomyJob.STATUS_PENDING)

        # Second enqueue for same version
        job2, created2 = TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=1)
        self.assertFalse(created2)
        self.assertEqual(job1.pk, job2.pk)

        # Only 1 job in database
        self.assertEqual(TaxonomyJob.objects.filter(business=self.biz).count(), 1)

    def test_claim_next_job_sets_lease_and_increments_attempts(self):
        """Claiming a pending job sets lease owner and expiration, and increments attempts to 1."""
        TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=1)

        job = TaxonomyJobRepository.claim_next_job(worker_id="worker-node-1", lease_seconds=60)
        self.assertIsNotNone(job)
        self.assertEqual(job.status, TaxonomyJob.STATUS_RUNNING)
        self.assertEqual(job.lease_owner, "worker-node-1")
        self.assertEqual(job.attempts, 1)
        self.assertGreater(job.lease_expires_at, timezone.now())

        # Second claim with no other jobs returns None (skip locked)
        job_none = TaxonomyJobRepository.claim_next_job(worker_id="worker-node-2")
        self.assertIsNone(job_none)

    def test_crash_recovery_resets_expired_leases(self):
        """If a worker crashes with a leased job, recover_expired_leases resets it to PENDING."""
        TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=1)
        job = TaxonomyJobRepository.claim_next_job(worker_id="crashed-worker", lease_seconds=10)

        # Artificially expire the lease
        past = timezone.now() - timedelta(minutes=5)
        TaxonomyJob.objects.filter(pk=job.pk).update(lease_expires_at=past)

        # Another worker claims next job -> triggers expired lease recovery
        recovered_job = TaxonomyJobRepository.claim_next_job(worker_id="new-worker-2", lease_seconds=60)
        self.assertIsNotNone(recovered_job)
        self.assertEqual(recovered_job.pk, job.pk)
        self.assertEqual(recovered_job.lease_owner, "new-worker-2")
        self.assertEqual(recovered_job.attempts, 2)  # Attempt incremented to 2

    def test_poison_pill_protection_moves_to_dead_letter(self):
        """Repeated failures reaching max_attempts (3) are moved to DEAD_LETTER, not retried infinitely."""
        TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=1)

        # Fail attempt 1
        j1 = TaxonomyJobRepository.claim_next_job(worker_id="w1")
        TaxonomyJobRepository.fail_job(j1.pk, "Error 1")
        j1.refresh_from_db()
        self.assertEqual(j1.status, TaxonomyJob.STATUS_PENDING)

        # Fail attempt 2
        j2 = TaxonomyJobRepository.claim_next_job(worker_id="w2")
        TaxonomyJobRepository.fail_job(j2.pk, "Error 2")
        j2.refresh_from_db()
        self.assertEqual(j2.status, TaxonomyJob.STATUS_PENDING)

        # Fail attempt 3 (reaches max_attempts = 3)
        j3 = TaxonomyJobRepository.claim_next_job(worker_id="w3")
        TaxonomyJobRepository.fail_job(j3.pk, "Fatal poison pill error")
        j3.refresh_from_db()
        self.assertEqual(j3.status, TaxonomyJob.STATUS_DEAD_LETTER)
        self.assertIn("Fatal poison pill error", j3.error_message)

        # Next claim returns None (dead letter job is NOT claimed)
        self.assertIsNone(TaxonomyJobRepository.claim_next_job(worker_id="w4"))

    def test_complete_job_records_results(self):
        """Completing a job sets COMPLETED status and persists result JSON."""
        TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=1)
        job = TaxonomyJobRepository.claim_next_job(worker_id="w1")

        result_payload = {"organized": 5, "created_nodes": 2}
        TaxonomyJobRepository.complete_job(job.pk, result_payload)

        job.refresh_from_db()
        self.assertEqual(job.status, TaxonomyJob.STATUS_COMPLETED)
        self.assertEqual(job.result, result_payload)
        self.assertIsNone(job.lease_owner)
