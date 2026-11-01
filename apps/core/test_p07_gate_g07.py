import os
import subprocess
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone

import psycopg

from apps.businesses.models import Business
from apps.products.models import Product, TaxonomyJob
from apps.products.taxonomy import maybe_organize
from apps.products.taxonomy_jobs import TaxonomyJobRepository
from apps.products.taxonomy_worker import run_taxonomy_worker_once, process_taxonomy_job
from apps.billing.models import CreditWaitingQueue
from need_engine.store import Store

User = get_user_model()


class GateG07SchemaAndJobsTestCase(TestCase):
    """
    Gate G07 Exit Verification Matrix:
    1. R25 explicitly documented as DEFERRED; legacy instagram_engine completely frozen.
    2. Zero DDL statements executed in active request paths.
    3. Taxonomy jobs are durable, leased, version-safe, and thread-daemon free.
    4. Taxonomy job infrastructure does not reinvent or conflict with CreditWaitingQueue.
    5. Crash recovery and lease drain mechanisms operate cleanly.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="g07_admin", email="g07@test.local")
        self.biz = Business.objects.create(name="G07 Business", user=self.user)

    def test_g07_r25_explicitly_deferred_and_legacy_frozen(self):
        """R25 is officially documented as DEFERRED and instagram_engine remains completely frozen."""
        possible_paths = [
            os.path.join(os.path.dirname(__file__), "R25_DEFERRAL_AND_LEGACY_GUARD.md"),
            os.path.join(os.path.dirname(__file__), "../../docs/remediation/R25_DEFERRAL_AND_LEGACY_GUARD.md"),
        ]
        doc_path = next((p for p in possible_paths if os.path.exists(p)), None)
        self.assertIsNotNone(doc_path, "R25 deferral guard document must exist")

        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("EXPLICITLY DEFERRED", content)
        self.assertIn("R25", content)

        # Enforce that instagram_engine/models.py exists and preserves frozen SQLAlchemy declarations
        legacy_models_path = os.path.join(
            os.path.dirname(__file__),
            "../../instagram_engine/models.py",
        )
        self.assertTrue(os.path.exists(legacy_models_path), "instagram_engine/models.py must exist")
        with open(legacy_models_path, "r", encoding="utf-8") as f:
            legacy_code = f.read()
        self.assertIn("class Base(DeclarativeBase):", legacy_code)
        self.assertIn("class IGAccount(Base):", legacy_code)
        # Ensure it has not been modified to use Django ORM
        self.assertNotIn("models.Model", legacy_code)

    def test_g07_zero_ddl_in_active_runtime_request_paths(self):
        """Active Store initialization in runtime paths executes ZERO DDL statements."""
        db_conf = connection.settings_dict
        dsn = f"postgresql://{db_conf['USER']}:{db_conf['PASSWORD']}@{db_conf['HOST']}:{db_conf['PORT']}/{db_conf['NAME']}"

        executed_statements = []
        real_connect = psycopg.connect

        def spied_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            real_exec = conn.execute

            def tracking_exec(query, *exec_args, **exec_kwargs):
                query_str = str(query).strip().upper()
                executed_statements.append(query_str)
                return real_exec(query, *exec_args, **exec_kwargs)

            conn.execute = tracking_exec
            return conn

        with patch("need_engine.store.psycopg.connect", side_effect=spied_connect):
            store = Store(dsn, schema="test_g07_preflight", create=False)
            store.close()

        ddl_keywords = ["CREATE TABLE", "ALTER TABLE", "CREATE EXTENSION", "CREATE INDEX"]
        for stmt in executed_statements:
            for kw in ddl_keywords:
                self.assertNotIn(
                    kw,
                    stmt,
                    f"DDL keyword '{kw}' executed in active runtime path: {stmt}",
                )

    def test_g07_taxonomy_jobs_durable_leased_versioned_no_daemons(self):
        """Taxonomy uses durable leased jobs without spawning unmanaged daemon threads."""
        for i in range(5):
            Product.objects.create(business=self.biz, name=f"G07 Prod {i+1}", status="ACTIVE")

        with patch("threading.Thread") as mock_thread:
            triggered = maybe_organize(self.biz, background=True)
            self.assertTrue(triggered)
            mock_thread.assert_not_called()

        job = TaxonomyJob.objects.filter(business=self.biz).first()
        self.assertIsNotNone(job)
        self.assertEqual(job.status, TaxonomyJob.STATUS_PENDING)
        self.assertEqual(job.catalog_version, 5)

        # Worker claims job with lease
        claimed = TaxonomyJobRepository.claim_next_job(worker_id="worker_g07", lease_seconds=60)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.status, TaxonomyJob.STATUS_RUNNING)
        self.assertEqual(claimed.lease_owner, "worker_g07")

    def test_g07_credit_waiting_queue_separation(self):
        """TaxonomyJob queue is completely independent from CreditWaitingQueue."""
        # Taxonomy job operates on catalog organisation
        tax_job, _ = TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=1)
        self.assertEqual(TaxonomyJob.objects.filter(pk=tax_job.pk).count(), 1)

        # CreditWaitingQueue remains financial-only
        self.assertFalse(CreditWaitingQueue.objects.filter(operation_id=tax_job.job_key).exists())

    def test_g07_worker_crash_and_lease_drain(self):
        """Expired lease from a crashed worker is recovered safely by next claim."""
        job, _ = TaxonomyJobRepository.enqueue_job(self.biz, catalog_version=2)
        claimed = TaxonomyJobRepository.claim_next_job(worker_id="crashed_node")

        # Simulate crash: lease expires
        past = timezone.now() - timedelta(minutes=10)
        TaxonomyJob.objects.filter(pk=claimed.pk).update(lease_expires_at=past)

        # Drain / recovery
        recovered = TaxonomyJobRepository.recover_expired_leases()
        self.assertEqual(recovered, 1)

        claimed.refresh_from_db()
        self.assertEqual(claimed.status, TaxonomyJob.STATUS_PENDING)
        self.assertIsNone(claimed.lease_owner)
