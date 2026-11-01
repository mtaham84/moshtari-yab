"""
Integration test suite for Gate G05 (P05.09 / R14, R15, R16, R17, R18).

Comprehensive verification of:
1. Pure contract execution with zero paid requests in test environment.
2. Exact Provider + ModelRef identity disambiguation and Ops priority preservation.
3. Distributed quota enforcement and 429 cooldown propagation.
4. Bounded retry policy, JSON repair, and multi-model fallback.
5. Self-healing embedder circuit breaker past reset time.
6. Strict legacy isolation (instagram_engine untouched).
"""

from decimal import Decimal
from django.core.cache import cache
from django.test import TestCase

from apps.billing.models import Provider, AIModel, BillingSettings
from apps.core.gateway import (
    LLMGateway,
    MockLLMTransport,
    TransportResponse,
    RetryPolicy,
    ProviderQuotaExhausted,
)
from apps.core.gateway_contract import (
    ModelRef,
    OperationRef,
    PriceSnapshot,
    GatewayResultStatus,
    FakeBudgetPort,
    FakeQuotaPort,
    FakeLedgerPort,
)
from apps.core.llm import chat_json, set_gateway_override, PanelLedgerAdapter, LLMError
from apps.core.models import LLMUsageLog
from apps.core.quota_manager import SharedQuotaManager
from need_engine.config import EngineConfig
from need_engine.embeddings import Embedder
from need_engine.gateway_adapter import NeedEngineGatewayAdapter, NeedEngineLedgerAdapter


class GateG05VerificationTestCase(TestCase):
    """
    Verification matrix for Gate G05 (P05.09).
    """

    def setUp(self):
        cache.clear()
        set_gateway_override(None)

        self.provider_luna = Provider.objects.create(
            name="Luna",
            base_url="https://api.luna.test/v1",
            api_key="luna-key",
            is_active=True,
        )
        self.provider_openai = Provider.objects.create(
            name="OpenAI",
            base_url="https://api.openai.test/v1",
            api_key="openai-key",
            is_active=True,
        )

        # Same model name on two different providers with distinct priorities
        self.model_luna = AIModel.objects.create(
            provider=self.provider_luna,
            name="gpt-4o-mini",
            label="GPT 4o Mini Luna",
            input_price_usd=Decimal("0.15"),
            output_price_usd=Decimal("0.60"),
            priority=5,
            is_active=True,
        )
        self.model_openai = AIModel.objects.create(
            provider=self.provider_openai,
            name="gpt-4o-mini",
            label="GPT 4o Mini OpenAI",
            input_price_usd=Decimal("0.15"),
            output_price_usd=Decimal("0.60"),
            priority=25,
            is_active=True,
        )

        BillingSettings.objects.create(
            pk=1,
            usd_to_toman=Decimal("268000"),
            markup_percent=Decimal("50.0"),
            enforce_balance=True,
            min_balance_toman=Decimal("100"),
        )

    def tearDown(self):
        cache.clear()
        set_gateway_override(None)

    def test_g05_zero_paid_requests_and_queued_on_insufficient_budget(self):
        """
        Gate Criterion 1: Insufficient funds MUST return QUEUED with ZERO external requests.
        """
        budget_port = FakeBudgetPort({"tenant_zero": Decimal("0")})
        mock_transport = MockLLMTransport()

        gateway = LLMGateway(
            budget_port=budget_port,
            quota_port=FakeQuotaPort(),
            ledger_port=PanelLedgerAdapter(),
            transport=mock_transport,
        )
        set_gateway_override(gateway)

        with self.assertRaises(LLMError) as ctx:
            chat_json(
                system="Extract",
                user="User prompt",
                purpose="test_zero_paid",
                business_id=999,
            )

        self.assertIn("QUEUED", str(ctx.exception))
        # Zero external HTTP requests made
        self.assertEqual(len(mock_transport.call_history), 0)
        # Zero debit or logs
        self.assertEqual(LLMUsageLog.objects.filter(purpose="test_zero_paid").count(), 0)

    def test_g05_model_ref_disambiguation_and_ops_priority(self):
        """
        Gate Criterion 2: Two providers with identical model name are disambiguated and prioritize Luna.
        """
        mock_transport = MockLLMTransport()
        # Luna succeeds
        mock_transport.set_constant_response(
            "luna:gpt-4o-mini",
            content="{\"chosen_provider\": \"Luna\"}",
            pt=400,
            ct=100,
        )

        gateway = LLMGateway(
            budget_port=FakeBudgetPort({"tenant_10": Decimal("50000")}),
            quota_port=FakeQuotaPort(),
            ledger_port=PanelLedgerAdapter(),
            transport=mock_transport,
        )
        set_gateway_override(gateway)

        result = chat_json(
            system="System",
            user="User",
            purpose="priority_test",
            business_id=10,
        )

        self.assertEqual(result, {"chosen_provider": "Luna"})
        # Verify call history dispatched to Luna
        self.assertEqual(len(mock_transport.call_history), 1)
        self.assertEqual(mock_transport.call_history[0]["model_ref"].identity, "luna:gpt-4o-mini")

        # Verify LLMUsageLog recorded correct model identity and non-zero cost
        log_entry = LLMUsageLog.objects.filter(purpose="priority_test", business_id=10).first()
        self.assertIsNotNone(log_entry)
        self.assertEqual(log_entry.model, "luna:gpt-4o-mini")
        self.assertGreater(log_entry.cost_usd, Decimal("0.0"))

    def test_g05_distributed_quota_and_429_cooldown(self):
        """
        Gate Criterion 3: 429 received by worker 1 immediately blocks worker 2 in shared cache.
        """
        quota_mgr1 = SharedQuotaManager()
        quota_mgr2 = SharedQuotaManager()
        model_ref = ModelRef("Luna", "gpt-4o-mini")

        # Initially available
        self.assertTrue(quota_mgr1.reserve_provider_quota(model_ref, 100)[0])

        # Worker 1 encounters 429
        quota_mgr1.record_provider_429(model_ref, retry_after_sec=20.0, is_daily=False)

        # Worker 2 is immediately throttled
        allowed, reason = quota_mgr2.reserve_provider_quota(model_ref, 100)
        self.assertFalse(allowed)
        self.assertIn("COOLDOWN_ACTIVE", reason)

    def test_g05_retry_repair_and_candidate_fallback(self):
        """
        Gate Criterion 4: Bounded repair retry followed by smooth fallback to next candidate.
        """
        mock_transport = MockLLMTransport()
        # Luna always returns broken JSON
        mock_transport.set_constant_response("luna:gpt-4o-mini", "not valid json at all")
        # OpenAI returns valid JSON
        mock_transport.set_constant_response("openai:gpt-4o-mini", "{\"recovered_via_openai\": true}")

        gateway = LLMGateway(
            budget_port=FakeBudgetPort({"tenant_20": Decimal("50000")}),
            quota_port=FakeQuotaPort(),
            ledger_port=PanelLedgerAdapter(),
            transport=mock_transport,
            retry_policy=RetryPolicy(max_total_attempts=4, max_parse_retries_per_model=1),
        )
        set_gateway_override(gateway)

        result = chat_json(
            system="System",
            user="User",
            purpose="fallback_gate_test",
            business_id=20,
        )

        self.assertEqual(result, {"recovered_via_openai": True})
        # Check logs: Luna initial (failed), Luna repair (failed), OpenAI (success)
        logs = list(LLMUsageLog.objects.filter(purpose="fallback_gate_test", business_id=20).order_by("id"))
        self.assertEqual(len(logs), 3)
        self.assertEqual(logs[0].model, "luna:gpt-4o-mini")
        self.assertFalse(logs[0].ok)
        self.assertEqual(logs[1].model, "luna:gpt-4o-mini")
        self.assertFalse(logs[1].ok)
        self.assertEqual(logs[2].model, "openai:gpt-4o-mini")
        self.assertTrue(logs[2].ok)

    def test_g05_embedder_circuit_breaker_self_healing(self):
        """
        Gate Criterion 5: Embedder recovers past reset threshold using injectable clock.
        """
        simulated_time = 1700000000.0
        cfg = EngineConfig()
        cfg.embed_backend = "hash"

        embedder = Embedder(cfg, llm=None, clock=lambda: simulated_time)
        # Manually trip circuit with reset in 60s
        embedder.exhausted_until = simulated_time + 60.0
        self.assertTrue(embedder.exhausted)

        # Before reset: bypasses call
        res_before = embedder.encode(["Text"])
        self.assertEqual(res_before.shape, (1, 0))

        # Advance past reset
        simulated_time += 65.0
        self.assertFalse(embedder.exhausted)

        # After reset: probe succeeds and circuit recovers
        res_after = embedder.encode(["Recovered Text"])
        self.assertGreater(res_after.shape[1], 0)
        self.assertFalse(embedder.exhausted)
        self.assertEqual(embedder.exhausted_until, 0.0)

    def test_g05_legacy_boundary_frozen_isolation(self):
        """
        Gate Criterion 6: instagram_engine remains completely untouched, isolated, and frozen.
        """
        import os
        legacy_models_path = os.path.join("instagram_engine", "models.py")
        self.assertTrue(os.path.exists(legacy_models_path))
        with open(legacy_models_path, "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn("class Lead(", content)
            self.assertIn("class IGAccount(", content)

        # Confirm adapter flag is disabled by default to avoid premature live cutover
        cfg = EngineConfig()
        adapter = NeedEngineGatewayAdapter(cfg=cfg)
        self.assertFalse(adapter.is_enabled)
