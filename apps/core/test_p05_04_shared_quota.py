"""
Unit test suite for Shared Distributed Quota Manager (P05.04 / R15).
"""

from django.core.cache import cache
from django.test import SimpleTestCase

from apps.core.gateway_contract import ModelRef
from apps.core.quota_manager import SharedQuotaManager


class SharedQuotaTestCase(SimpleTestCase):
    """
    Validates cross-process quota synchronization and 429 cooldown propagation.
    """

    def setUp(self):
        cache.clear()
        self.model = ModelRef("Luna", "gemini-1.5-flash")

    def tearDown(self):
        cache.clear()

    def test_cross_process_cooldown_throttles_second_worker(self):
        """
        R15: When Worker 1 receives 429, Worker 2 must immediately be throttled via shared cache.
        """
        worker1 = SharedQuotaManager(default_limits={"rpm": 60, "tpm": 100000, "rpd": 1000})
        worker2 = SharedQuotaManager(default_limits={"rpm": 60, "tpm": 100000, "rpd": 1000})

        # Initially Worker 1 and Worker 2 can reserve quota
        allowed1, _ = worker1.reserve_provider_quota(self.model, 500)
        self.assertTrue(allowed1)

        # Worker 1 encounters HTTP 429 from provider
        worker1.record_provider_429(self.model, retry_after_sec=15.0, is_daily=False)

        # Worker 2 attempts to send request to the same provider
        allowed2, reason2 = worker2.reserve_provider_quota(self.model, 500)
        self.assertFalse(allowed2)
        self.assertIn("COOLDOWN_ACTIVE", reason2)

    def test_rpm_limit_enforced_across_instances(self):
        """
        R15: Separate worker instances coordinate on shared RPM ceiling.
        """
        limits = {"rpm": 3, "tpm": 100000, "rpd": 1000}
        worker1 = SharedQuotaManager(default_limits=limits)
        worker2 = SharedQuotaManager(default_limits=limits)

        # 3 calls permitted in current minute
        self.assertTrue(worker1.reserve_provider_quota(self.model, 100)[0])
        self.assertTrue(worker2.reserve_provider_quota(self.model, 100)[0])
        self.assertTrue(worker1.reserve_provider_quota(self.model, 100)[0])

        # 4th call exceeds RPM
        allowed, reason = worker2.reserve_provider_quota(self.model, 100)
        self.assertFalse(allowed)
        self.assertEqual(reason, "RPM_EXCEEDED")

    def test_tpm_limit_enforced_across_instances(self):
        """
        R15: Separate worker instances coordinate on shared TPM ceiling.
        """
        limits = {"rpm": 100, "tpm": 1000, "rpd": 1000}
        worker1 = SharedQuotaManager(default_limits=limits)
        worker2 = SharedQuotaManager(default_limits=limits)

        # Worker 1 reserves 600 tokens
        self.assertTrue(worker1.reserve_provider_quota(self.model, 600)[0])

        # Worker 2 tries to reserve 500 tokens (600 + 500 > 1000)
        allowed, reason = worker2.reserve_provider_quota(self.model, 500)
        self.assertFalse(allowed)
        self.assertEqual(reason, "TPM_EXCEEDED")

    def test_daily_rpd_limit_enforced_across_instances(self):
        """
        R15: Separate worker instances coordinate on daily RPD limit.
        """
        limits = {"rpm": 100, "tpm": 100000, "rpd": 2}
        worker1 = SharedQuotaManager(default_limits=limits)
        worker2 = SharedQuotaManager(default_limits=limits)

        # First 2 requests succeed
        self.assertTrue(worker1.reserve_provider_quota(self.model, 100)[0])
        self.assertTrue(worker2.reserve_provider_quota(self.model, 100)[0])

        # 3rd request fails daily limit
        allowed, reason = worker1.reserve_provider_quota(self.model, 100)
        self.assertFalse(allowed)
        self.assertEqual(reason, "DAILY_RPD_EXHAUSTED")

    def test_daily_429_sets_daily_exhaustion_across_workers(self):
        """
        R15: Provider message indicating daily limit exhaustion persists for all workers.
        """
        worker1 = SharedQuotaManager(default_limits={"rpm": 100, "tpm": 100000, "rpd": 5000})
        worker2 = SharedQuotaManager(default_limits={"rpm": 100, "tpm": 100000, "rpd": 5000})

        worker1.record_provider_429(self.model, is_daily=True)

        allowed, reason = worker2.reserve_provider_quota(self.model, 100)
        self.assertFalse(allowed)
        self.assertEqual(reason, "DAILY_RPD_EXHAUSTED")
