"""One place for the web app's direct model calls (taxonomy building, product organising ...).

Routed through LLMGateway (P05.07 / R07, R14), parsing JSON robustly and recording
accurate USD cost calculations to LLMUsageLog without altering caller signatures.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from decimal import Decimal
from typing import Any, Optional, Tuple

log = logging.getLogger("core.llm")

# Test override gateway hook
_gateway_override: Optional[Any] = None


def set_gateway_override(gateway: Optional[Any]) -> None:
    global _gateway_override
    _gateway_override = gateway


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


class PanelLedgerAdapter:
    """
    LedgerPort adapter that writes to Django LLMUsageLog with accurate non-zero cost (R14).
    """

    def record_attempt(self, operation: Any, attempt: Any, price_snapshot: Any, ref: str) -> str:
        from apps.core.models import LLMUsageLog
        from apps.core.gateway_contract import GatewayResultStatus

        tokens_in = attempt.usage.prompt_tokens if attempt.usage else 0
        tokens_out = attempt.usage.completion_tokens if attempt.usage else 0
        cost_usd = attempt.usage.cost_usd if attempt.usage else Decimal("0.0")
        is_ok = attempt.status == GatewayResultStatus.SUCCESS

        b_id = None
        if operation.payer_tenant_id.startswith("tenant_"):
            try:
                b_id = int(operation.payer_tenant_id.replace("tenant_", ""))
            except ValueError:
                pass

        try:
            log_entry = LLMUsageLog.objects.create(
                model=attempt.model_ref.identity[:80],
                purpose=operation.kind.replace("panel_", ""),
                business_id=b_id,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
                ok=is_ok,
            )
            return str(log_entry.id)
        except Exception as exc:
            from apps.core.typed_errors import sanitize_text
            log.warning("Failed to create LLMUsageLog: %s", sanitize_text(exc))
            return "log_err"


class PanelBudgetAdapter:
    """
    BudgetPort adapter for panel calls prior to Phase 6 real wallet debit.
    Enforces minimum balance if balance enforcement is active.
    """

    def reserve(self, operation: Any, estimated_toman: Decimal) -> Tuple[bool, str, Optional[str]]:
        from apps.billing.models import BillingSettings, Wallet

        try:
            settings = BillingSettings.get()
            if settings.enforce_balance and operation.payer_tenant_id.startswith("tenant_"):
                b_id = int(operation.payer_tenant_id.replace("tenant_", ""))
                wallet = Wallet.objects.filter(business_id=b_id).first()
                if wallet and wallet.balance_toman < settings.min_balance_toman:
                    return False, "INSUFFICIENT_FUNDS", None
        except Exception as exc:
            log.warning("Panel budget check error: %s", exc)

        return True, "ALLOWED", f"panel_res_{uuid.uuid4().hex[:8]}"

    def settle(self, reservation_id: str, actual_toman: Decimal) -> bool:
        return True

    def release(self, reservation_id: str) -> bool:
        return True

    def reconcile(self, reservation_id: str) -> str:
        return "SETTLED"


def chat_json(
    system: str,
    user: str,
    purpose: str,
    business_id: int | None = None,
    max_tokens: int = 3500,
    timeout: float = 90.0,
    attempts: int = 2,
) -> Any:
    """Runs one JSON-returning chat completion; raises LLMError when no usable JSON comes back."""
    from apps.billing.models import AIModel, BillingSettings
    from apps.core.gateway import LLMGateway, RetryPolicy
    from apps.core.gateway_contract import (
        GatewayResultStatus,
        ModelRef,
        OperationRef,
        PriceSnapshot,
    )
    from apps.core.quota_manager import SharedQuotaManager

    payer_tenant = f"tenant_{business_id}" if business_id else "platform_system"
    op = OperationRef.create(kind=f"panel_{purpose}", payer_tenant_id=payer_tenant)

    # 1. Fetch active candidate models ordered by Ops priority
    models = list(
        AIModel.objects.filter(is_active=True, provider__is_active=True)
        .select_related("provider")
        .order_by("priority", "id")
    )

    candidates: list[ModelRef] = []
    candidate_endpoints: dict[str, tuple[str, str]] = {}

    for m in models:
        if "embed" in m.name.lower():
            continue
        if m.provider and m.provider.base_url and m.provider.api_key:
            ref = m.model_ref
            candidates.append(ref)
            candidate_endpoints[ref.identity] = (m.provider.base_url.rstrip("/"), m.provider.api_key)

    # Fallback to instagram_engine helper if candidates table is completely empty
    if not candidates and _gateway_override is None:
        try:
            from instagram_engine.analyzer import _call_llm_with_usage

            payload = {
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "temperature": 0.2,
                "max_tokens": max_tokens,
            }
            res = _call_llm_with_usage(payload, timeout=timeout)
            content = res.get("content")
            if content:
                return _extract_json(content)
        except Exception:
            pass
        raise LLMError("هیچ ارائه‌دهنده یا مدل هوش مصنوعی فعالی یافت نشد.")

    # 2. Build Price Snapshot
    try:
        b_settings = BillingSettings.get()
        usd_to_toman = b_settings.usd_to_toman
        markup = b_settings.markup_percent
        charge_cached = b_settings.charge_cached
    except Exception:
        usd_to_toman = Decimal("268000")
        markup = Decimal("0.0")
        charge_cached = False

    p_in = models[0].input_price_usd if models else Decimal("0.15")
    p_out = models[0].output_price_usd if models else Decimal("0.60")

    snapshot = PriceSnapshot(
        input_price_usd_per_1m=p_in,
        output_price_usd_per_1m=p_out,
        usd_to_toman=usd_to_toman,
        markup_percent=markup,
        charge_cached=charge_cached,
    )

    # 3. Use Override Gateway or Production LLMGateway
    if _gateway_override is not None:
        gateway = _gateway_override
    else:
        from django.conf import settings
        use_real_finance = getattr(settings, "USE_REAL_FINANCIAL_GATEWAY", False)
        if use_real_finance:
            from apps.billing.wallet_budget_service import DjangoWalletBudgetService
            from apps.billing.cost_ledger import DjangoCostLedger
            budget_port = DjangoWalletBudgetService()
            ledger_port = DjangoCostLedger()
        else:
            budget_port = PanelBudgetAdapter()
            ledger_port = PanelLedgerAdapter()

        gateway = LLMGateway(
            budget_port=budget_port,
            quota_port=SharedQuotaManager(),
            ledger_port=ledger_port,
            retry_policy=RetryPolicy(max_total_attempts=max(attempts, 2)),
        )

    # 4. Execute via LLMGateway
    result = gateway.execute(
        operation=op,
        system_prompt=system,
        user_prompt=user,
        price_snapshot=snapshot,
        candidates=candidates,
        candidate_endpoints=candidate_endpoints,
        max_tokens=max_tokens,
        timeout=timeout,
        is_json=True,
    )

    if result.status == GatewayResultStatus.SUCCESS:
        if result.parsed_json is not None:
            return result.parsed_json
        return _extract_json(result.content or "")

    if result.status == GatewayResultStatus.QUEUED:
        if business_id:
            try:
                from apps.billing.credit_queue_service import CreditQueueService
                from apps.businesses.models import Business
                b = Business.objects.filter(pk=business_id).first()
                if b:
                    CreditQueueService.enqueue_waiting_work(
                        business=b,
                        operation_id=op.operation_id,
                        stage=f"panel_{purpose}",
                        payload={"system": system, "user": user, "max_tokens": max_tokens},
                        estimated_toman=Decimal("500.00"),
                    )
            except Exception as ex:
                log.warning("Failed to enqueue queued operation %s: %s", op.operation_id, ex)
        raise LLMError("اعتبار کافی برای انجام این عملیات در حساب موجود نیست (QUEUED).")

    if result.status == GatewayResultStatus.UNAVAILABLE:
        raise LLMError("سرویس‌دهنده‌های هوش مصنوعی در حال حاضر در دسترس نیستند.")

    from apps.core.typed_errors import sanitize_text
    clean_err = sanitize_text(result.error_message or "خطا در پردازش هوش مصنوعی")
    raise LLMError(clean_err)
