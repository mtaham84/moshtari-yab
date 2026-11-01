from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model

from apps.businesses.models import Business
from apps.billing.models import (
    AIModel,
    BillingSettings,
    CostEvent,
    CreditWaitingQueue,
    Provider,
    Wallet,
    WalletReservation,
)
from apps.core.gateway import LLMGateway, MockLLMTransport, RetryPolicy
from apps.core.gateway_contract import (
    GatewayResultStatus,
    ModelRef,
    OperationRef,
    PriceSnapshot,
)
from apps.core.quota_manager import SharedQuotaManager
from apps.billing.wallet_budget_service import DjangoWalletBudgetService
from apps.billing.cost_ledger import DjangoCostLedger
from apps.core.llm import chat_json, LLMError

User = get_user_model()


class RealFinancialGatewayTestCase(TestCase):
    """
    P06.07 Verification: Connecting gateway and active endpoints to real finance,
    zero paid calls on insufficient budget, queueing into CreditWaitingQueue,
    and strict Ops fallback (R07, R14, R15, R32).
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="fin_gateway_user", email="fingw@test.local")
        self.biz = Business.objects.create(name="Fin Gateway Biz", user=self.user)
        self.wallet, _ = Wallet.objects.get_or_create(business=self.biz)
        self.wallet.balance_toman = Decimal("0.00")
        self.wallet.save(update_fields=["balance_toman"])

        self.settings = BillingSettings.get()
        self.settings.enforce_balance = True
        self.settings.min_balance_toman = Decimal("0.00")
        self.settings.usd_to_toman = Decimal("268000")
        self.settings.markup_percent = Decimal("50.0")
        self.settings.save()

        # Create providers and models in Ops priority order
        self.prov_gemini = Provider.objects.create(name="GeminiOps", base_url="https://gemini.test.local", api_key="gem-key")
        self.prov_openai = Provider.objects.create(name="OpenAIOps", base_url="https://openai.test.local", api_key="oai-key")

        self.m1 = AIModel.objects.create(
            provider=self.prov_gemini,
            name="gemini-1.5-flash",
            priority=10,
            input_price_usd=Decimal("0.10"),
            output_price_usd=Decimal("0.20"),
        )
        self.m2 = AIModel.objects.create(
            provider=self.prov_openai,
            name="gpt-4o-mini",
            priority=20,
            input_price_usd=Decimal("0.15"),
            output_price_usd=Decimal("0.60"),
        )

        self.budget_port = DjangoWalletBudgetService()
        self.ledger_port = DjangoCostLedger()
        self.quota_port = SharedQuotaManager()

    def test_zero_budget_makes_zero_paid_calls_and_returns_queued(self):
        """When business wallet has zero budget, gateway makes 0 network calls and returns QUEUED."""
        mock_transport = MockLLMTransport()
        gateway = LLMGateway(
            budget_port=self.budget_port,
            quota_port=self.quota_port,
            ledger_port=self.ledger_port,
            transport=mock_transport,
        )

        op = OperationRef("op-zero-budget", "need_extraction", str(self.biz.pk))
        snapshot = PriceSnapshot(Decimal("0.10"), Decimal("0.20"), Decimal("268000"), Decimal("50.0"))

        res = gateway.execute(
            operation=op,
            system_prompt="sys",
            user_prompt="user",
            price_snapshot=snapshot,
            candidates=[self.m1.model_ref, self.m2.model_ref],
        )

        self.assertEqual(res.status, GatewayResultStatus.QUEUED)
        # ZERO network calls executed!
        self.assertEqual(len(mock_transport.call_history), 0)
        # ZERO wallet debits or reservations
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("0.00"))
        self.assertFalse(WalletReservation.objects.filter(operation_id="op-zero-budget").exists())

    def test_topped_up_balance_reserves_funds_and_executes_ops_priority(self):
        """After wallet top-up, reservation succeeds and gateway calls highest Ops priority model."""
        self.wallet.balance_toman = Decimal("10000.00")
        self.wallet.save(update_fields=["balance_toman"])

        mock_transport = MockLLMTransport()
        mock_transport.set_constant_response(
            "geminiops:gemini-1.5-flash",
            '{"result": "extracted_from_gemini"}',
            pt=200,
            ct=50,
        )

        gateway = LLMGateway(
            budget_port=self.budget_port,
            quota_port=self.quota_port,
            ledger_port=self.ledger_port,
            transport=mock_transport,
        )

        op = OperationRef("op-funded", "need_extraction", str(self.biz.pk))
        snapshot = PriceSnapshot(Decimal("0.10"), Decimal("0.20"), Decimal("268000"), Decimal("50.0"))

        res = gateway.execute(
            operation=op,
            system_prompt="sys",
            user_prompt="user",
            price_snapshot=snapshot,
            candidates=[self.m1.model_ref, self.m2.model_ref],
            is_json=True,
        )

        self.assertEqual(res.status, GatewayResultStatus.SUCCESS)
        self.assertEqual(res.parsed_json, {"result": "extracted_from_gemini"})
        # Called model 1 (priority 10)
        self.assertEqual(len(mock_transport.call_history), 1)
        self.assertEqual(mock_transport.call_history[0]["model_ref"].model_name, "gemini-1.5-flash")

        # Authoritative cost event was logged
        evt = CostEvent.objects.filter(operation_id="op-funded").first()
        self.assertIsNotNone(evt)
        self.assertEqual(evt.provider_name, "GeminiOps")
        self.assertEqual(evt.model_name, "gemini-1.5-flash")
        self.assertGreater(evt.total_cost_toman, Decimal("0.00"))

    @override_settings(USE_REAL_FINANCIAL_GATEWAY=True)
    def test_panel_chat_json_enqueues_to_credit_waiting_queue_when_unfunded(self):
        """Panel endpoint chat_json enqueues unfunded operation into CreditWaitingQueue with zero paid calls."""
        self.wallet.balance_toman = Decimal("0.00")
        self.wallet.save(update_fields=["balance_toman"])

        with self.assertRaises(LLMError) as ctx:
            chat_json(
                system="system instruction",
                user="user prompt",
                purpose="taxonomy",
                business_id=self.biz.pk,
            )

        self.assertIn("QUEUED", str(ctx.exception))

        # Check that work item was enqueued in CreditWaitingQueue
        item = CreditWaitingQueue.objects.filter(business=self.biz, status=CreditWaitingQueue.STATUS_WAITING).first()
        self.assertIsNotNone(item)
        self.assertEqual(item.stage, "panel_taxonomy")
        self.assertEqual(item.payload["system"], "system instruction")
