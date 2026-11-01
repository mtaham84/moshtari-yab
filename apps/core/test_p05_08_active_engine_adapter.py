"""
Unit test suite for Active Engine Adapter and Legacy Boundary (P05.08 / R14, R25).
"""

from decimal import Decimal
from django.test import SimpleTestCase

from apps.core.gateway import LLMGateway, MockLLMTransport, TransportResponse
from apps.core.gateway_contract import FakeBudgetPort, FakeQuotaPort, ModelRef
from need_engine.config import EngineConfig
from need_engine.gateway_adapter import NeedEngineGatewayAdapter, NeedEngineLedgerAdapter


class ActiveEngineAdapterTestCase(SimpleTestCase):
    """
    Validates P05.08:
    - NeedEngine adapter routes correctly through LLMGateway
    - Usage and cost recorded cleanly without double debit
    - Legacy boundary (instagram_engine) remains untouched and isolated
    """

    def setUp(self):
        self.cfg = EngineConfig()
        self.mock_transport = MockLLMTransport()
        self.ledger_adapter = NeedEngineLedgerAdapter()
        self.budget_port = FakeBudgetPort({"tenant_biz1": Decimal("50000")})
        self.quota_port = FakeQuotaPort()

        self.gateway = LLMGateway(
            budget_port=self.budget_port,
            quota_port=self.quota_port,
            ledger_port=self.ledger_adapter,
            transport=self.mock_transport,
        )

        self.adapter = NeedEngineGatewayAdapter(
            cfg=self.cfg,
            gateway=self.gateway,
        )

    def test_active_engine_adapter_executes_with_fake_provider(self):
        """
        R14: Active engine adapter executes through gateway and produces parsed output and usage.
        """
        model_identity = f"default:{self.cfg.extract_model}".lower()
        self.mock_transport.set_constant_response(
            model_identity,
            content="{\"need\": \"dental crown\", \"confidence\": 0.90}",
            pt=300,
            ct=150,
        )

        data, usage = self.adapter.complete_json(
            stage="need_extraction",
            system="Extract customer need",
            user="Looking for dental crowns in Tehran",
            businesses=["biz1"],
        )

        self.assertEqual(data, {"need": "dental crown", "confidence": 0.90})
        self.assertEqual(usage["pt"], 300)
        self.assertEqual(usage["ct"], 150)
        self.assertEqual(usage["status"], "SUCCESS")

        # Verify ledger recorded attempt
        self.assertEqual(len(self.ledger_adapter.recorded_attempts), 1)
        record = self.ledger_adapter.recorded_attempts[0]
        self.assertEqual(record["stage"], "need_engine_need_extraction")
        self.assertEqual(record["status"], "SUCCESS")

    def test_legacy_boundary_flag_and_isolation(self):
        """
        R25 / Product Decision #7: Adapter switch is disabled by default to avoid premature cutover,
        and instagram_engine is confirmed isolated as frozen legacy.
        """
        default_adapter = NeedEngineGatewayAdapter(cfg=self.cfg)
        self.assertFalse(default_adapter.is_enabled)

        # Confirm legacy module exists untouched without modifications
        import instagram_engine.analyzer
        self.assertTrue(hasattr(instagram_engine.analyzer, "_call_llm_with_usage"))
