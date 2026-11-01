import logging
from datetime import timedelta
from typing import Optional, Tuple

from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business
from apps.products.models import TaxonomyJob

log = logging.getLogger(__name__)


class TaxonomyJobRepository:
    """
    Authoritative durable job repository for taxonomy organisation (P07.03 / R24).
    Replaces unmanaged threading.Thread daemon with leased, idempotent, retry-limited jobs.
    """

    @classmethod
    @transaction.atomic
    def enqueue_job(
        cls,
        business: Business,
        catalog_version: int,
        force: bool = False,
    ) -> Tuple[TaxonomyJob, bool]:
        """
        Enqueues a taxonomy organisation job for a business and catalog version.
        Idempotent: Multiple concurrent requests for the same (business, version) collapse into
        one single pending job.
        Returns (job, created).
        """
        job_key = f"tax_{business.pk}_v{catalog_version}"

        existing = TaxonomyJob.objects.select_for_update().filter(job_key=job_key).first()
        if existing:
            # If still pending or running, reuse without duplicating
            if existing.status in (TaxonomyJob.STATUS_PENDING, TaxonomyJob.STATUS_RUNNING) and not force:
                return existing, False
            if existing.status == TaxonomyJob.STATUS_COMPLETED and not force:
                return existing, False

            # If failed or forced, reset to pending
            existing.status = TaxonomyJob.STATUS_PENDING
            existing.attempts = 0
            existing.lease_owner = None
            existing.lease_expires_at = None
            existing.error_message = ""
            existing.save(update_fields=["status", "attempts", "lease_owner", "lease_expires_at", "error_message", "updated_at"])
            return existing, False

        job = TaxonomyJob.objects.create(
            job_key=job_key,
            business=business,
            catalog_version=catalog_version,
            status=TaxonomyJob.STATUS_PENDING,
        )
        log.info("Enqueued TaxonomyJob %s for business %s (version %s)", job_key, business.pk, catalog_version)
        return job, True

    @classmethod
    @transaction.atomic
    def claim_next_job(
        cls,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> Optional[TaxonomyJob]:
        """
        Claims the next pending job or recovers an expired leased job using SELECT FOR UPDATE SKIP LOCKED.
        Returns claimed job or None.
        """
        now = timezone.now()

        # 1. First recover any expired leases
        cls.recover_expired_leases()

        # 2. Claim next available pending job
        job = (
            TaxonomyJob.objects.select_for_update(skip_locked=True)
            .filter(status=TaxonomyJob.STATUS_PENDING)
            .order_by("created_at")
            .first()
        )

        if not job:
            return None

        job.status = TaxonomyJob.STATUS_RUNNING
        job.lease_owner = worker_id
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.attempts += 1
        job.save(update_fields=["status", "lease_owner", "lease_expires_at", "attempts", "updated_at"])
        return job

    @classmethod
    @transaction.atomic
    def complete_job(cls, job_id: int, result: dict) -> None:
        """
        Marks job as COMPLETED and records the result payload.
        """
        job = TaxonomyJob.objects.select_for_update().filter(pk=job_id).first()
        if job:
            job.status = TaxonomyJob.STATUS_COMPLETED
            job.result = result
            job.lease_owner = None
            job.lease_expires_at = None
            job.save(update_fields=["status", "result", "lease_owner", "lease_expires_at", "updated_at"])
            log.info("Completed TaxonomyJob %s", job.job_key)

    @classmethod
    @transaction.atomic
    def fail_job(cls, job_id: int, error_message: str) -> None:
        """
        Marks job as FAILED or DEAD_LETTER (if attempts exceed max_attempts).
        Prevents poison pill infinite retries.
        """
        job = TaxonomyJob.objects.select_for_update().filter(pk=job_id).first()
        if not job:
            return

        job.error_message = (error_message or "")[:1000]
        job.lease_owner = None
        job.lease_expires_at = None

        if job.attempts >= job.max_attempts:
            job.status = TaxonomyJob.STATUS_DEAD_LETTER
            log.warning("TaxonomyJob %s moved to DEAD_LETTER after %d failed attempts: %s", job.job_key, job.attempts, error_message)
        else:
            job.status = TaxonomyJob.STATUS_PENDING
            log.info("TaxonomyJob %s failed attempt %d, reset to PENDING for retry: %s", job.job_key, job.attempts, error_message)

        job.save(update_fields=["status", "error_message", "lease_owner", "lease_expires_at", "updated_at"])

    @classmethod
    @transaction.atomic
    def recover_expired_leases(cls) -> int:
        """
        Identifies RUNNING jobs where lease_expires_at < now and resets them to PENDING or DEAD_LETTER.
        Returns count of recovered jobs.
        """
        now = timezone.now()
        expired_jobs = list(
            TaxonomyJob.objects.select_for_update(skip_locked=True)
            .filter(status=TaxonomyJob.STATUS_RUNNING, lease_expires_at__lt=now)
        )

        recovered_count = 0
        for job in expired_jobs:
            recovered_count += 1
            if job.attempts >= job.max_attempts:
                job.status = TaxonomyJob.STATUS_DEAD_LETTER
                job.error_message = f"Lease expired after {job.attempts} attempts; marked DEAD_LETTER"
            else:
                job.status = TaxonomyJob.STATUS_PENDING
            job.lease_owner = None
            job.lease_expires_at = None
            job.save(update_fields=["status", "lease_owner", "lease_expires_at", "error_message", "updated_at"])

        if recovered_count:
            log.info("Recovered %d expired taxonomy job leases.", recovered_count)
        return recovered_count
