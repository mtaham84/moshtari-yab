"""
Unit test suite for Injectable LLM Gateway (P05.03 / R14, R41).
"""

from decimal import Decimal
from django.test import SimpleTestCase

from apps.core.gateway import (
    LLMGateway,
    MockLLMTransport,
    TransportResponse,
    ProviderQuotaExhausted,
    ProviderNetworkError,
    ProviderResponseError,
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


class GatewayInjectableTestCase(SimpleTestCase):
    """
    Validates P05.03:
    - Injectable mock transport
    - Multi-model fallback
    - Granular error classification (429, timeout, 5xx, JSON error)
    - Zero network calls on insufficient budget (QUEUED)
    - Accurate financial settlement and attempt logging
    """

    def setUp(self):
        self.budget_port = FakeBudgetPort({"tenant_test": Decimal("10000")})
        self.quota_port = FakeQuotaPort()
        self.ledger_port = FakeLedgerPort()
        self.transport = MockLLMTransport()

        self.gateway = LLMGateway(
            budget_port=self.budget_port,
            quota_port=self.quota_port,
            ledger_port=self.ledger_port,
            transport=self.transport,
        )

        self.price_snapshot = PriceSnapshot(
            input_price_usd_per_1m=Decimal("0.15"),
            output_price_usd_per_1m=Decimal("0.60"),
            usd_to_toman=Decimal("268000"),
            markup_percent=Decimal("50.0"),  # multiplier = 1.5
        )

        self.op = OperationRef.create(kind="extract", payer_tenant_id="tenant_test")
        self.model_primary = ModelRef("Luna", "gemini-1.5-flash")
        self.model_secondary = ModelRef("OpenAI", "gpt-4o-mini")

    def test_successful_json_execution(self):
        """
        R14: Valid JSON output parses cleanly, settles budget, and records success in ledger.
        """
        self.transport.set_constant_response(
            self.model_primary.identity,
            content="```json\n{\"category\": \"dentistry\", \"confidence\": 0.95}\n```",
            pt=200,
            ct=100,
        )

        result = self.gateway.execute(
            operation=self.op,
            system_prompt="You are a classifier",
            user_prompt="I need implants",
            price_snapshot=self.price_snapshot,
            candidates=[self.model_primary],
        )

        self.assertEqual(result.status, GatewayResultStatus.SUCCESS)
        self.assertIsNotNone(result.parsed_json)
        self.assertEqual(result.parsed_json.get("category"), "dentistry")
        self.assertEqual(result.usage.prompt_tokens, 200)
        self.assertEqual(result.usage.completion_tokens, 100)
        self.assertTrue(result.usage.is_exact_usage)
        self.assertGreater(result.usage.cost_toman, Decimal("0"))

        # Budget settled and ledger recorded
        self.assertEqual(len(self.ledger_port.records), 1)
        self.assertEqual(self.ledger_port.records[0]["status"], "SUCCESS")
        self.assertIn(result.reservation_id, self.budget_port.settled)

    def test_malformed_json_fallback_to_secondary_model(self):
        """
        R16: Invalid JSON on primary model triggers fallback to secondary model.
        """
        # Primary returns unparseable garbage
        self.transport.set_constant_response(
            self.model_primary.identity,
            content="I am sorry but I cannot return valid JSON here <<unclosed",
        )
        # Secondary returns valid JSON
        self.transport.set_constant_response(
            self.model_secondary.identity,
            content="{\"valid\": true}",
            pt=150,
            ct=50,
        )

        result = self.gateway.execute(
            operation=self.op,
            system_prompt="Return JSON",
            user_prompt="Extract",
            price_snapshot=self.price_snapshot,
            candidates=[self.model_primary, self.model_secondary],
        )

        self.assertEqual(result.status, GatewayResultStatus.SUCCESS)
        self.assertEqual(result.parsed_json, {"valid": True})
        self.assertEqual(result.model_ref, self.model_secondary)

        # 3 attempts were executed (initial + repair on primary, then fallback on secondary)
        self.assertEqual(len(result.attempts), 3)
        self.assertEqual(result.attempts[0].status, GatewayResultStatus.FAILED)
        self.assertEqual(result.attempts[1].status, GatewayResultStatus.FAILED)
        self.assertEqual(result.attempts[2].status, GatewayResultStatus.SUCCESS)

    def test_network_timeout_marked_unknown(self):
        """
        R41: Socket timeout marks attempt status UNKNOWN without unhandled crash.
        """
        self.transport.set_error(
            self.model_primary.identity,
            ProviderNetworkError("Socket timeout during connection"),
        )

        result = self.gateway.execute(
            operation=self.op,
            system_prompt="Test",
            user_prompt="Timeout",
            price_snapshot=self.price_snapshot,
            candidates=[self.model_primary],
        )

        self.assertEqual(result.status, GatewayResultStatus.UNKNOWN)
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].status, GatewayResultStatus.UNKNOWN)
        # Ledger records the network timeout
        self.assertEqual(self.ledger_port.records[0]["status"], "UNKNOWN")

    def test_quota_429_engages_cooldown_and_falls_back(self):
        """
        R15: 429 response notifies quota port and activates fallback candidate.
        """
        self.transport.set_error(
            self.model_primary.identity,
            ProviderQuotaExhausted("Rate limit exceeded 429"),
        )
        self.transport.set_constant_response(
            self.model_secondary.identity,
            content="{\"recovered\": true}",
        )

        result = self.gateway.execute(
            operation=self.op,
            system_prompt="Test",
            user_prompt="Quota",
            price_snapshot=self.price_snapshot,
            candidates=[self.model_primary, self.model_secondary],
        )

        self.assertEqual(result.status, GatewayResultStatus.SUCCESS)
        self.assertEqual(result.model_ref, self.model_secondary)
        # Primary model quota was recorded as daily exhausted or backoff
        self.assertIn(self.model_primary.identity, self.quota_port.exhausted_models)

    def test_insufficient_budget_returns_queued_without_network_io(self):
        """
        Product Decision #2: Insufficient balance returns QUEUED with 0 transport calls.
        """
        # Tenant has 0 balance
        self.budget_port.set_balance("tenant_broke", Decimal("0"))
        broke_op = OperationRef.create(kind="reply", payer_tenant_id="tenant_broke")

        result = self.gateway.execute(
            operation=broke_op,
            system_prompt="Write pitch",
            user_prompt="Prompt",
            price_snapshot=self.price_snapshot,
            candidates=[self.model_primary],
        )

        self.assertEqual(result.status, GatewayResultStatus.QUEUED)
        self.assertIn("Insufficient funds", result.error_message)
        # ZERO network calls dispatched
        self.assertEqual(len(self.transport.call_history), 0)
        # Zero ledger records
        self.assertEqual(len(self.ledger_port.records), 0)
