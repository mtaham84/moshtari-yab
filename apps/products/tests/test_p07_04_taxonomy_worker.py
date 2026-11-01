from io import StringIO
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import transaction
from django.core.management import call_command

from apps.businesses.models import Business
from apps.products.models import Product, TaxonomyJob
from apps.products.taxonomy import maybe_organize
from apps.products.taxonomy_jobs import TaxonomyJobRepository
from apps.products.taxonomy_worker import run_taxonomy_worker_once, process_taxonomy_job

User = get_user_model()


class TaxonomyWorkerTestCase(TestCase):
    """
    P07.04 Verification: Replacing daemon threads with versioned, durable jobs,
    outbox transaction safety, and catalog version staleness protection (R24).
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="tax_worker_user", email="tw@test.local")
        self.biz = Business.objects.create(name="Tax Worker Biz", user=self.user)

        # Create 5 active products to trigger ORGANIZE_EVERY (5)
        for i in range(5):
            Product.objects.create(
                business=self.biz,
                name=f"Product {i+1}",
                status="ACTIVE",
            )

    def test_maybe_organize_enqueues_durable_job_without_daemon_threads(self):
        """maybe_organize enqueues a persistent TaxonomyJob instead of spawning threads."""
        with patch("threading.Thread") as mock_thread:
            triggered = maybe_organize(self.biz, background=True)

            self.assertTrue(triggered)
            # ZERO threading.Thread spawned!
            mock_thread.assert_not_called()

        # TaxonomyJob exists in DB
        job = TaxonomyJob.objects.filter(business=self.biz).first()
        self.assertIsNotNone(job)
        self.assertEqual(job.status, TaxonomyJob.STATUS_PENDING)
        self.assertEqual(job.catalog_version, 5)

    def test_stale_catalog_version_does_not_overwrite_newer_catalog(self):
        """An older job whose catalog_version < organized_product_count is skipped cleanly."""
        # Enqueue job for version 5
        job, _ = TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=5)

        # Simulate that business catalog was already reorganized at version 10
        self.biz.organized_product_count = 10
        self.biz.save(update_fields=["organized_product_count"])

        # Process the old job
        with patch("apps.products.taxonomy_worker.organize_products") as mock_organize:
            processed = process_taxonomy_job(job.pk)

            self.assertTrue(processed)
            # organize_products was NOT called because job is stale!
            mock_organize.assert_not_called()

        job.refresh_from_db()
        self.assertEqual(job.status, TaxonomyJob.STATUS_COMPLETED)
        self.assertEqual(job.result.get("skipped"), "stale_version")

    def test_transaction_rollback_creates_zero_phantom_jobs(self):
        """If a product addition transaction rolls back, the enqueued job is also rolled back."""
        try:
            with transaction.atomic():
                Product.objects.create(business=self.biz, name="Product 6", status="ACTIVE")
                TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=6)
                # Force rollback
                raise RuntimeError("Simulated transaction rollback")
        except RuntimeError:
            pass

        # Job for version 6 does not exist!
        self.assertFalse(TaxonomyJob.objects.filter(business=self.biz, catalog_version=6).exists())

    def test_run_taxonomy_worker_once_executes_pending_job(self):
        """run_taxonomy_worker_once claims, executes, and completes the pending job."""
        TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=5)

        with patch("apps.products.taxonomy_worker.organize_products") as mock_organize:
            mock_organize.return_value = {"placed": 3, "created": 1}
            ran = run_taxonomy_worker_once(worker_id="test_worker")

            self.assertTrue(ran)
            mock_organize.assert_called_once_with(self.biz)

        job = TaxonomyJob.objects.filter(business=self.biz).first()
        self.assertEqual(job.status, TaxonomyJob.STATUS_COMPLETED)
        self.assertEqual(job.result, {"placed": 3, "created": 1})

    def test_process_taxonomy_jobs_management_command(self):
        """Management command process_taxonomy_jobs drains the queue cleanly."""
        TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=5)

        out = StringIO()
        with patch("apps.products.taxonomy_worker.organize_products") as mock_organize:
            mock_organize.return_value = {"placed": 5, "created": 0}
            call_command("process_taxonomy_jobs", "--max-jobs=5", stdout=out)

        output = out.getvalue()
        self.assertIn("Starting taxonomy worker", output)
        self.assertIn("Successfully processed 1 taxonomy jobs", output)
