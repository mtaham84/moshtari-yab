"""
Unit and integration test suite for Panel LLM Adapter and chat_json (P05.07 / R07, R14).
"""

from decimal import Decimal
from django.test import TestCase

from apps.billing.models import Provider, AIModel, BillingSettings
from apps.core.gateway import LLMGateway, MockLLMTransport, TransportResponse
from apps.core.gateway_contract import FakeBudgetPort, FakeQuotaPort
from apps.core.llm import chat_json, set_gateway_override, PanelLedgerAdapter, LLMError
from apps.core.models import LLMUsageLog


class PanelAdapterTestCase(TestCase):
    """
    Validates P05.07:
    - Backward-compatible chat_json signature
    - Accurate non-zero cost_usd recording in LLMUsageLog (fixing R14)
    - Full identity attribution in model name
    - Queued behavior on insufficient budget
    """

    def setUp(self):
        set_gateway_override(None)

        self.provider = Provider.objects.create(
            name="Luna",
            base_url="https://api.luna.test/v1",
            api_key="luna-secret-key",
            is_active=True,
        )
        self.model = AIModel.objects.create(
            provider=self.provider,
            name="gemini-flash",
            label="Gemini Flash",
            input_price_usd=Decimal("0.20"),
            output_price_usd=Decimal("0.80"),
            priority=1,
            is_active=True,
        )
        BillingSettings.objects.create(
            pk=1,
            usd_to_toman=Decimal("268000"),
            markup_percent=Decimal("50.0"),
            enforce_balance=False,
        )

    def tearDown(self):
        set_gateway_override(None)

    def test_chat_json_successful_execution_and_accurate_cost_logging(self):
        """
        R14: chat_json returns parsed JSON and records accurate non-zero cost_usd in LLMUsageLog.
        """
        mock_transport = MockLLMTransport()
        mock_transport.set_constant_response(
            self.model.identity,
            content="{\"extracted\": true, \"summary\": \"High quality dental implants\"}",
            pt=1000,
            ct=500,
        )

        gateway = LLMGateway(
            budget_port=FakeBudgetPort({"tenant_42": Decimal("100000")}),
            quota_port=FakeQuotaPort(),
            ledger_port=PanelLedgerAdapter(),
            transport=mock_transport,
        )
        set_gateway_override(gateway)

        # Call chat_json using standard signature
        result = chat_json(
            system="Extract summary",
            user="Patient needs implants",
            purpose="product_summary",
            business_id=42,
        )

        self.assertEqual(result, {"extracted": True, "summary": "High quality dental implants"})

        # Verify LLMUsageLog was created with non-zero cost_usd
        log_entry = LLMUsageLog.objects.filter(business_id=42, purpose="product_summary").first()
        self.assertIsNotNone(log_entry)
        self.assertEqual(log_entry.model, "luna:gemini-flash")
        self.assertEqual(log_entry.tokens_in, 1000)
        self.assertEqual(log_entry.tokens_out, 500)
        self.assertTrue(log_entry.ok)

        # 1000/1e6 * 0.20 + 500/1e6 * 0.80 = 0.00020 + 0.00040 = 0.000600 USD
        self.assertEqual(log_entry.cost_usd, Decimal("0.000600"))

    def test_chat_json_insufficient_funds_raises_queued_error(self):
        """
        Product Decision #2 / R31: Insufficient funds in budget port raises LLMError with QUEUED.
        """
        budget_port = FakeBudgetPort({"tenant_99": Decimal("0")})  # 0 balance
        gateway = LLMGateway(
            budget_port=budget_port,
            quota_port=FakeQuotaPort(),
            ledger_port=PanelLedgerAdapter(),
            transport=MockLLMTransport(),
        )
        set_gateway_override(gateway)

        with self.assertRaises(LLMError) as ctx:
            chat_json(
                system="Extract",
                user="User",
                purpose="tax_build",
                business_id=99,
            )

        self.assertIn("QUEUED", str(ctx.exception))
        # Zero usage log created for queued rejection
        self.assertEqual(LLMUsageLog.objects.filter(business_id=99).count(), 0)

    def test_chat_json_multi_model_fallback(self):
        """
        R16: If primary candidate fails, chat_json falls back to secondary candidate model.
        """
        provider_openai = Provider.objects.create(
            name="OpenAI",
            base_url="https://api.openai.test/v1",
            api_key="openai-secret-key",
            is_active=True,
        )
        model_secondary = AIModel.objects.create(
            provider=provider_openai,
            name="gpt-4o-mini",
            label="GPT 4o Mini",
            priority=10,  # Lower priority than Luna
            is_active=True,
        )

        mock_transport = MockLLMTransport()
        # Primary fails with malformed JSON
        mock_transport.set_constant_response(self.model.identity, "Not valid JSON <<unclosed>>")
        # Secondary succeeds
        mock_transport.set_constant_response(
            model_secondary.identity,
            content="{\"fallback_ok\": true}",
            pt=300,
            ct=100,
        )

        gateway = LLMGateway(
            budget_port=FakeBudgetPort({"tenant_55": Decimal("50000")}),
            quota_port=FakeQuotaPort(),
            ledger_port=PanelLedgerAdapter(),
            transport=mock_transport,
        )
        set_gateway_override(gateway)

        result = chat_json(
            system="Analyze",
            user="Data",
            purpose="fallback_test",
            business_id=55,
        )

        self.assertEqual(result, {"fallback_ok": True})

        # Logs must reflect both attempts: failed attempt 1 on Luna, successful attempt on OpenAI
        logs = list(LLMUsageLog.objects.filter(business_id=55).order_by("id"))
        self.assertGreaterEqual(len(logs), 2)
        # Final log should be for OpenAI
        self.assertEqual(logs[-1].model, "openai:gpt-4o-mini")
        self.assertTrue(logs[-1].ok)
