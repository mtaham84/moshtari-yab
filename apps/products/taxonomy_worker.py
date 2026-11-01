import logging
import uuid
from typing import Optional

from django.db import transaction

from apps.businesses.models import Business
from apps.products.models import Product, TaxonomyJob
from apps.products.taxonomy import organize_products
from apps.products.taxonomy_jobs import TaxonomyJobRepository

log = logging.getLogger(__name__)


def process_taxonomy_job(job_id: int) -> bool:
    """
    Safely executes a single claimed or specific TaxonomyJob (P07.04 / R24).
    Enforces catalog version protection: Older jobs cannot overwrite newer catalog state.
    """
    job = TaxonomyJob.objects.filter(pk=job_id).first()
    if not job:
        return False

    biz = job.business
    biz.refresh_from_db(fields=["organized_product_count"])

    # Version staleness protection:
    # If business catalog has already advanced past this job's catalog_version,
    # skip AI execution to avoid overwriting newer organization.
    if job.catalog_version < biz.organized_product_count:
        log.info(
            "Skipping stale TaxonomyJob %s: job version %d < current organized count %d",
            job.job_key, job.catalog_version, biz.organized_product_count,
        )
        TaxonomyJobRepository.complete_job(job.pk, {
            "skipped": "stale_version",
            "job_version": job.catalog_version,
            "current_organized": biz.organized_product_count,
        })
        return True

    try:
        stats = organize_products(biz)
        TaxonomyJobRepository.complete_job(job.pk, stats)
        return True
    except Exception as exc:
        log.exception("TaxonomyJob %s execution failed: %s", job.job_key, exc)
        TaxonomyJobRepository.fail_job(job.pk, str(exc))
        return False


def run_taxonomy_worker_once(worker_id: Optional[str] = None, lease_seconds: int = 120) -> bool:
    """
    Claims and executes the next pending taxonomy job.
    Returns True if a job was processed, False if queue was empty.
    """
    w_id = worker_id or f"tax_worker_{uuid.uuid4().hex[:8]}"
    job = TaxonomyJobRepository.claim_next_job(worker_id=w_id, lease_seconds=lease_seconds)
    if not job:
        return False

    return process_taxonomy_job(job.pk)
