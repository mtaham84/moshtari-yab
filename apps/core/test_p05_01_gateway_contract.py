"""
Unit test suite for Gateway, Ledger, and Budget Contracts (P05.01 / R14, R15, R17, R31, R32, R34).
"""

from decimal import Decimal
from django.test import SimpleTestCase

from apps.core.gateway_contract import (
    ModelRef,
    PriceSnapshot,
    OperationRef,
    AttemptRef,
    UsageResult,
    GatewayResultStatus,
    GatewayResult,
    FakeBudgetPort,
    FakeQuotaPort,
    FakeLedgerPort,
)


class GatewayContractTestCase(SimpleTestCase):
    """
    Validates domain types and port contracts for P05.01.
    """

    def test_model_ref_identity_disambiguation(self):
        """
        R17: AI models with the same name from different providers MUST NOT collide.
        """
        ref_luna = ModelRef(provider_name="Luna", model_name="gpt-4o-mini")
        ref_openai = ModelRef(provider_name="OpenAI", model_name="gpt-4o-mini")
        ref_deployed = ModelRef(provider_name="Azure", model_name="gpt-4o-mini", deployment_id="dep-123")

        self.assertEqual(ref_luna.identity, "luna:gpt-4o-mini")
        self.assertEqual(ref_openai.identity, "openai:gpt-4o-mini")
        self.assertEqual(ref_deployed.identity, "azure:gpt-4o-mini:dep-123")

        self.assertNotEqual(ref_luna.identity, ref_openai.identity)
        self.assertNotEqual(ref_luna, ref_openai)

    def test_price_snapshot_cost_calculation_uncached(self):
        """
        R14: Deterministic USD and Toman computation according to active rate snapshot.
        """
        snapshot = PriceSnapshot(
            input_price_usd_per_1m=Decimal("0.15"),
            output_price_usd_per_1m=Decimal("0.60"),
            usd_to_toman=Decimal("268000"),
            markup_percent=Decimal("50.0"),  # multiplier = 1.5
            charge_cached=False,
        )

        # 1,000 prompt tokens, 500 completion tokens
        cost_usd = snapshot.compute_cost_usd(1000, 500, is_cached=False)
        # 1000/1e6 * 0.15 = 0.00015; 500/1e6 * 0.60 = 0.00030 -> total = 0.000450 USD
        self.assertEqual(cost_usd, Decimal("0.000450"))

        cost_toman = snapshot.compute_cost_toman(cost_usd)
        # 0.00045 * 268000 * 1.5 = 180.9 -> quantize to 181 Toman
        self.assertEqual(cost_toman, Decimal("181"))

    def test_price_snapshot_cached_free_policy(self):
        """
        R14: Cached response costs 0 when charge_cached is False, but charges when True.
        """
        free_snapshot = PriceSnapshot(
            input_price_usd_per_1m=Decimal("1.0"),
            output_price_usd_per_1m=Decimal("2.0"),
            usd_to_toman=Decimal("268000"),
            markup_percent=Decimal("0.0"),
            charge_cached=False,
        )
        self.assertEqual(free_snapshot.compute_cost_usd(1000, 500, is_cached=True), Decimal("0.0"))

        charged_snapshot = PriceSnapshot(
            input_price_usd_per_1m=Decimal("1.0"),
            output_price_usd_per_1m=Decimal("2.0"),
            usd_to_toman=Decimal("268000"),
            markup_percent=Decimal("0.0"),
            charge_cached=True,
        )
        self.assertGreater(charged_snapshot.compute_cost_usd(1000, 500, is_cached=True), Decimal("0.0"))

    def test_budget_port_insufficient_balance_returns_queued(self):
        """
        Product Decision #2 / R31: Insufficient funds MUST return allowed=False and reason=INSUFFICIENT_FUNDS.
        NO debt, NO unauthorized fallback calls.
        """
        budget_port = FakeBudgetPort({"tenant_alpha": Decimal("50")})  # Balance is 50 Toman
        op = OperationRef.create(kind="extract", payer_tenant_id="tenant_alpha")

        # Estimated cost is 100 Toman (exceeds balance of 50)
        allowed, reason, res_id = budget_port.reserve(op, estimated_toman=Decimal("100"))

        self.assertFalse(allowed)
        self.assertEqual(reason, "INSUFFICIENT_FUNDS")
        self.assertIsNone(res_id)
        # Balance remains unchanged
        self.assertEqual(budget_port.balances["tenant_alpha"], Decimal("50"))

    def test_budget_port_reservation_and_settlement_lifecycle(self):
        """
        R31: Funds are locked during reservation and settled accurately on completion.
        """
        budget_port = FakeBudgetPort({"tenant_alpha": Decimal("1000")})
        op = OperationRef.create(kind="reply", payer_tenant_id="tenant_alpha")

        # Reserve 300 Toman
        allowed, reason, res_id = budget_port.reserve(op, estimated_toman=Decimal("300"))
        self.assertTrue(allowed)
        self.assertEqual(budget_port.balances["tenant_alpha"], Decimal("700"))

        # Actual cost turned out to be 200 Toman (100 Toman refund expected)
        success = budget_port.settle(res_id, actual_toman=Decimal("200"))
        self.assertTrue(success)
        self.assertEqual(budget_port.balances["tenant_alpha"], Decimal("800"))
        self.assertEqual(budget_port.settled[res_id], Decimal("200"))

    def test_budget_port_release_on_cancellation(self):
        """
        R31: Releasing a reservation restores full reserved amount.
        """
        budget_port = FakeBudgetPort({"tenant_alpha": Decimal("500")})
        op = OperationRef.create(kind="verify", payer_tenant_id="tenant_alpha")

        allowed, _, res_id = budget_port.reserve(op, estimated_toman=Decimal("200"))
        self.assertTrue(allowed)
        self.assertEqual(budget_port.balances["tenant_alpha"], Decimal("300"))

        self.assertTrue(budget_port.release(res_id))
        self.assertEqual(budget_port.balances["tenant_alpha"], Decimal("500"))

    def test_quota_port_cooldown_and_exhaustion(self):
        """
        R15: Quota port blocks exhausted models and handles 429 backoff.
        """
        quota_port = FakeQuotaPort()
        model = ModelRef("google", "gemini-1.5-flash")

        # Initially available
        avail, _ = quota_port.reserve_provider_quota(model, 500)
        self.assertTrue(avail)

        # Mark daily exhausted
        quota_port.exhaust(model.identity)
        avail, reason = quota_port.reserve_provider_quota(model, 500)
        self.assertFalse(avail)
        self.assertEqual(reason, "DAILY_RPD_EXHAUSTED")

        # Unexhaust and record success
        quota_port.unexhaust(model.identity)
        quota_port.record_provider_success(model, 450)
        self.assertEqual(quota_port.recorded_tokens[model.identity], 450)

    def test_ledger_port_attempt_recording(self):
        """
        R14, R34: Every execution attempt is cleanly recorded in append-only ledger port.
        """
        ledger_port = FakeLedgerPort()
        op = OperationRef.create(kind="taxonomy", payer_tenant_id="tenant_beta")
        model = ModelRef("luna", "gemini-2.0")
        attempt = AttemptRef(
            attempt_id="att_1",
            operation_id=op.operation_id,
            attempt_number=1,
            model_ref=model,
            status=GatewayResultStatus.SUCCESS,
            usage=UsageResult(
                prompt_tokens=200,
                completion_tokens=100,
                total_tokens=300,
                cost_usd=Decimal("0.000100"),
                cost_toman=Decimal("40"),
            ),
        )
        snapshot = PriceSnapshot(
            input_price_usd_per_1m=Decimal("0.10"),
            output_price_usd_per_1m=Decimal("0.40"),
            usd_to_toman=Decimal("268000"),
            markup_percent=Decimal("0.0"),
        )

        rec_id = ledger_port.record_attempt(op, attempt, snapshot, ref="job_123")
        self.assertTrue(rec_id.startswith("led_"))
        self.assertEqual(len(ledger_port.records), 1)
        rec = ledger_port.records[0]
        self.assertEqual(rec["operation_id"], op.operation_id)
        self.assertEqual(rec["payer_tenant_id"], "tenant_beta")
        self.assertEqual(rec["model_identity"], "luna:gemini-2.0")
        self.assertEqual(rec["cost_toman"], Decimal("40"))
