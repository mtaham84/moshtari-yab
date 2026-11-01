"""
Unit test suite for Gateway Retry Policy, Parse Repair and Fallback (P05.05 / R16, R41).
"""

from decimal import Decimal
from django.test import SimpleTestCase

from apps.core.gateway import (
    LLMGateway,
    MockLLMTransport,
    TransportResponse,
    RetryPolicy,
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


class GatewayRetryAndParseTestCase(SimpleTestCase):
    """
    Validates P05.05:
    - Bounded retry policy (max_total_attempts)
    - Targeted JSON repair retry
    - Model fallback upon repair failure
    - Only successful attempt is debited
    """

    def setUp(self):
        self.budget_port = FakeBudgetPort({"tenant_alpha": Decimal("10000")})
        self.quota_port = FakeQuotaPort()
        self.ledger_port = FakeLedgerPort()
        self.transport = MockLLMTransport()

        self.retry_policy = RetryPolicy(
            max_total_attempts=3,
            max_parse_retries_per_model=1,
        )

        self.gateway = LLMGateway(
            budget_port=self.budget_port,
            quota_port=self.quota_port,
            ledger_port=self.ledger_port,
            transport=self.transport,
            retry_policy=self.retry_policy,
        )

        self.price_snapshot = PriceSnapshot(
            input_price_usd_per_1m=Decimal("0.20"),
            output_price_usd_per_1m=Decimal("0.80"),
            usd_to_toman=Decimal("268000"),
            markup_percent=Decimal("0.0"),
        )

        self.op = OperationRef.create(kind="verify", payer_tenant_id="tenant_alpha")
        self.model_1 = ModelRef("Provider1", "model-1")
        self.model_2 = ModelRef("Provider2", "model-2")
        self.model_3 = ModelRef("Provider3", "model-3")

    def test_json_repair_succeeds_on_same_model(self):
        """
        R16: Malformed JSON triggers exactly 1 repair attempt on the same model.
        """
        call_count = 0

        def handler(model_ref, messages):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call returns malformed JSON
                return TransportResponse("invalid json output >>>", 100, 50)
            # Second call (repair) returns valid JSON
            return TransportResponse("{\"fixed\": true}", 120, 20)

        self.transport.set_handler(self.model_1.identity, handler)

        result = self.gateway.execute(
            operation=self.op,
            system_prompt="Test",
            user_prompt="Prompt",
            price_snapshot=self.price_snapshot,
            candidates=[self.model_1, self.model_2],
        )

        self.assertEqual(result.status, GatewayResultStatus.SUCCESS)
        self.assertEqual(result.parsed_json, {"fixed": True})
        self.assertEqual(result.model_ref, self.model_1)
        # 2 attempts executed on model 1: attempt 1 failed, attempt 2 (repair) succeeded
        self.assertEqual(len(result.attempts), 2)
        self.assertEqual(result.attempts[0].status, GatewayResultStatus.FAILED)
        self.assertEqual(result.attempts[1].status, GatewayResultStatus.SUCCESS)

    def test_failed_repair_falls_back_to_next_candidate(self):
        """
        R16: When model 1 repair fails, gateway falls back to candidate 2 rather than looping.
        """
        # Model 1 always returns broken JSON
        self.transport.set_constant_response(
            self.model_1.identity,
            content="broken json permanently",
        )
        # Model 2 returns valid JSON on first attempt
        self.transport.set_constant_response(
            self.model_2.identity,
            content="{\"candidate_2\": true}",
        )

        result = self.gateway.execute(
            operation=self.op,
            system_prompt="Test",
            user_prompt="Prompt",
            price_snapshot=self.price_snapshot,
            candidates=[self.model_1, self.model_2],
        )

        self.assertEqual(result.status, GatewayResultStatus.SUCCESS)
        self.assertEqual(result.parsed_json, {"candidate_2": True})
        self.assertEqual(result.model_ref, self.model_2)

        # Total attempts:
        # 1. model 1 initial (FAILED)
        # 2. model 1 repair (FAILED)
        # 3. model 2 initial (SUCCESS)
        self.assertEqual(len(result.attempts), 3)
        self.assertEqual(result.attempts[0].model_ref, self.model_1)
        self.assertEqual(result.attempts[1].model_ref, self.model_1)
        self.assertEqual(result.attempts[2].model_ref, self.model_2)

    def test_max_total_attempts_cap_enforced(self):
        """
        R16: Total attempts across models and repairs never exceed max_total_attempts.
        """
        # All models return broken JSON
        self.transport.set_constant_response(self.model_1.identity, "bad")
        self.transport.set_constant_response(self.model_2.identity, "bad")
        self.transport.set_constant_response(self.model_3.identity, "bad")

        result = self.gateway.execute(
            operation=self.op,
            system_prompt="Test",
            user_prompt="Prompt",
            price_snapshot=self.price_snapshot,
            candidates=[self.model_1, self.model_2, self.model_3],
        )

        self.assertEqual(result.status, GatewayResultStatus.FAILED)
        # max_total_attempts is 3:
        # attempt 1: model 1 initial
        # attempt 2: model 1 repair
        # attempt 3: model 2 initial
        # loop breaks because len(attempts) >= 3, model 3 is not called!
        self.assertEqual(len(result.attempts), 3)
        self.assertLessEqual(len(result.attempts), self.retry_policy.max_total_attempts)

        # Budget is NOT debited on total failure (reservation is released)
        self.assertIn(result.reservation_id, self.budget_port.released)
        self.assertEqual(self.budget_port.balances["tenant_alpha"], Decimal("10000"))
