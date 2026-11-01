"""
Active Engine Adapter to LLMGateway (P05.08 / R14, R25).

Adapts need_engine callers to the unified gateway contract while strictly preserving
the frozen status of legacy components (instagram_engine) and avoiding double-debits.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from apps.core.gateway import LLMGateway, RetryPolicy
from apps.core.gateway_contract import (
    ModelRef,
    OperationRef,
    PriceSnapshot,
    GatewayResultStatus,
    BudgetPort,
    LedgerPort,
    QuotaPort,
)
from need_engine.config import EngineConfig
from need_engine.registry import ModelRegistry

log = logging.getLogger("need_engine.gateway_adapter")


class NeedEngineLedgerAdapter(LedgerPort):
    """
    Adapter recording attempts into need_engine ledger store without duplicate billing.
    """

    def __init__(self, store: Optional[Any] = None):
        self.store = store
        self.recorded_attempts: List[Dict[str, Any]] = []

    def record_attempt(
        self,
        operation: OperationRef,
        attempt: Any,
        price_snapshot: PriceSnapshot,
        ref: str,
    ) -> str:
        cost_usd = attempt.usage.cost_usd if attempt.usage else Decimal("0.0")
        cost_toman = attempt.usage.cost_toman if attempt.usage else Decimal("0.0")
        rec_id = f"ne_led_{attempt.attempt_id}"

        entry = {
            "id": rec_id,
            "operation_id": operation.operation_id,
            "stage": operation.kind,
            "model_identity": attempt.model_ref.identity,
            "status": attempt.status.value,
            "cost_usd": float(cost_usd),
            "cost_toman": float(cost_toman),
            "ref": ref,
        }
        self.recorded_attempts.append(entry)

        # If persistent store provided, mirror to need_engine store
        if self.store and attempt.status == GatewayResultStatus.SUCCESS and attempt.usage:
            try:
                self.store.add_cost(
                    operation.kind.replace("need_engine_", ""),
                    attempt.model_ref.model_name,
                    attempt.usage.prompt_tokens,
                    attempt.usage.completion_tokens,
                    attempt.usage.is_cached,
                    float(cost_usd),
                    float(cost_toman),
                    ref,
                )
            except Exception as exc:
                log.warning("Could not mirror cost to need_engine store: %s", exc)

        return rec_id


class NeedEngineBudgetAdapter(BudgetPort):
    """
    Budget port for active engine prior to Phase 6 real wallet debit.
    """

    def reserve(self, operation: OperationRef, estimated_toman: Decimal) -> Tuple[bool, str, Optional[str]]:
        return True, "ALLOWED", f"ne_res_{operation.operation_id[:8]}"

    def settle(self, reservation_id: str, actual_toman: Decimal) -> bool:
        return True

    def release(self, reservation_id: str) -> bool:
        return True

    def reconcile(self, reservation_id: str) -> str:
        return "SETTLED"


class NeedEngineGatewayAdapter:
    """
    Bridge connecting need_engine pipeline stages to LLMGateway.
    """

    def __init__(
        self,
        cfg: EngineConfig,
        registry: Optional[ModelRegistry] = None,
        gateway: Optional[LLMGateway] = None,
        store: Optional[Any] = None,
    ):
        self.cfg = cfg
        self.registry = registry or ModelRegistry(cfg)
        self.store = store
        self.is_enabled = getattr(cfg, "use_unified_gateway", False)

        if gateway is not None:
            self.gateway = gateway
        else:
            from apps.core.quota_manager import SharedQuotaManager

            use_real_finance = getattr(cfg, "use_real_financial_gateway", False)
            if use_real_finance:
                from apps.billing.wallet_budget_service import DjangoWalletBudgetService
                from apps.billing.cost_ledger import DjangoCostLedger
                budget_port = DjangoWalletBudgetService()
                ledger_port = DjangoCostLedger()
            else:
                budget_port = NeedEngineBudgetAdapter()
                ledger_port = NeedEngineLedgerAdapter(store=self.store)

            self.gateway = LLMGateway(
                budget_port=budget_port,
                quota_port=SharedQuotaManager(registry=self.registry),
                ledger_port=ledger_port,
                retry_policy=RetryPolicy(max_total_attempts=3),
            )

    def complete_json(
        self,
        stage: str,
        system: str,
        user: str,
        model: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        ref: str = "",
        businesses: Optional[List[Optional[str]]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """
        Executes stage through LLMGateway and returns (data, usage).
        """
        primary_payer = str(businesses[0]) if businesses and businesses[0] else "platform"
        op = OperationRef.create(kind=f"need_engine_{stage}", payer_tenant_id=primary_payer)

        # Determine candidates
        candidate_refs = self.registry.candidate_model_refs_for(stage)
        if not candidate_refs:
            fallback_ref = self.registry.model_ref_for(stage)
            if fallback_ref:
                candidate_refs = [fallback_ref]
            elif model:
                candidate_refs = [ModelRef("default", model)]
            else:
                default_m = getattr(self.cfg, "extract_model", "gpt-6-luna")
                candidate_refs = [ModelRef("default", default_m)]

        # Pricing snapshot
        first_model_name = candidate_refs[0].identity
        pin, pout = self.registry.price(first_model_name)
        snapshot = PriceSnapshot(
            input_price_usd_per_1m=Decimal(str(pin)),
            output_price_usd_per_1m=Decimal(str(pout)),
            usd_to_toman=Decimal(str(self.registry.usd_to_toman())),
            markup_percent=Decimal("0.0"),
            charge_cached=False,
        )

        result = self.gateway.execute(
            operation=op,
            system_prompt=system,
            user_prompt=user,
            price_snapshot=snapshot,
            candidates=candidate_refs,
            max_tokens=max_tokens,
            timeout=self.cfg.llm_timeout,
            is_json=True,
        )

        usage_dict = {
            "toman": float(result.usage.cost_toman) if result.usage else 0.0,
            "cost_usd": float(result.usage.cost_usd) if result.usage else 0.0,
            "cached": result.is_cached,
            "pt": result.usage.prompt_tokens if result.usage else 0,
            "ct": result.usage.completion_tokens if result.usage else 0,
            "status": result.status.value,
        }

        return result.parsed_json, usage_dict
