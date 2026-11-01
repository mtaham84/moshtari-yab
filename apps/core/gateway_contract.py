"""
Shared Gateway, Ledger and Budget Contract (P05.01 / R14, R15, R17, R31, R32, R34).

Defines the formal domain contracts, interfaces (ports), and value objects for:
1. ModelRef: Unambiguous (provider, model, deployment) identity preventing name collisions.
2. OperationRef & AttemptRef: Traceable operational scope, payer attribution, and attempt lifecycle.
3. PriceSnapshot & UsageResult: Deterministic USD/Toman cost calculation, explicit cached/free accounting.
4. GatewayResultStatus: Strict outcome taxonomy (SUCCESS, QUEUED, UNAVAILABLE, UNKNOWN, FAILED).
5. Ports:
   - BudgetPort: reserve / settle / release / reconcile (Insufficient funds -> QUEUED, no debt, no leak).
   - QuotaPort: reserve_provider_quota / record_429 / record_success across workers.
   - LedgerPort: record_attempt / record_hold for immutable accounting.
6. In-Memory Fake Adapters for isolated Phase 5 testing prior to Phase 6 wallet/ledger integration.
"""

from __future__ import annotations

import abc
import enum
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple


class GatewayResultStatus(str, enum.Enum):
    """
    Standard outcome taxonomy for LLM Gateway operations.
    """
    SUCCESS = "SUCCESS"          # Completed successfully with valid response
    QUEUED = "QUEUED"            # Insufficient wallet balance / budget — zero external calls made
    UNAVAILABLE = "UNAVAILABLE"  # Providers down, circuit breaker open, or provider quotas exhausted
    UNKNOWN = "UNKNOWN"          # Network socket dropped during transmission — external fate unresolved
    FAILED = "FAILED"            # Hard failure (e.g. malformed JSON after maximum retries)


@dataclass(frozen=True)
class ModelRef:
    """
    Immutable specification of an AI Model with provider namespace disambiguation (R17).
    Prevents clashes when two distinct providers offer models with identical IDs.
    """
    provider_name: str
    model_name: str
    deployment_id: Optional[str] = None

    @property
    def identity(self) -> str:
        if self.deployment_id:
            return f"{self.provider_name}:{self.model_name}:{self.deployment_id}".lower()
        return f"{self.provider_name}:{self.model_name}".lower()

    def __str__(self) -> str:
        return self.identity


@dataclass(frozen=True)
class PriceSnapshot:
    """
    Point-in-time snapshot of pricing rates when an operation is scheduled.
    """
    input_price_usd_per_1m: Decimal
    output_price_usd_per_1m: Decimal
    usd_to_toman: Decimal
    markup_percent: Decimal
    charge_cached: bool = False
    snapshot_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def multiplier(self) -> Decimal:
        return Decimal("1.0") + (self.markup_percent / Decimal("100.0"))

    def compute_cost_usd(self, prompt_tokens: int, completion_tokens: int, is_cached: bool = False) -> Decimal:
        if is_cached and not self.charge_cached:
            return Decimal("0.0")
        in_cost = (Decimal(str(prompt_tokens)) / Decimal("1000000")) * self.input_price_usd_per_1m
        out_cost = (Decimal(str(completion_tokens)) / Decimal("1000000")) * self.output_price_usd_per_1m
        return (in_cost + out_cost).quantize(Decimal("0.000001"))

    def compute_cost_toman(self, cost_usd: Decimal) -> Decimal:
        raw_toman = cost_usd * self.usd_to_toman * self.multiplier
        return raw_toman.quantize(Decimal("1"))


@dataclass
class UsageResult:
    """
    Usage and financial attribution for an individual attempt or completed operation.
    """
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    is_cached: bool = False
    cost_usd: Decimal = Decimal("0.0")
    cost_toman: Decimal = Decimal("0.0")
    is_exact_usage: bool = True  # False if usage was estimated
    is_estimated: bool = False
    is_unknown: bool = False

    def __post_init__(self):
        if not self.is_exact_usage:
            object.__setattr__(self, "is_estimated", True)


@dataclass(frozen=True)
class OperationRef:
    """
    Represents a high-level domain operation requiring LLM execution.
    Tied explicitly to a payer tenant/business (R01, R14).
    """
    operation_id: str
    kind: str  # "extract", "verify", "reply", "taxonomy", "pitch", "embed"
    payer_tenant_id: str
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def create(cls, kind: str, payer_tenant_id: str, correlation_id: Optional[str] = None) -> OperationRef:
        return cls(
            operation_id=str(uuid.uuid4()),
            kind=kind,
            payer_tenant_id=payer_tenant_id,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )


@dataclass
class AttemptRef:
    """
    Represents an individual execution attempt against a specific ModelRef.
    """
    attempt_id: str
    operation_id: str
    attempt_number: int
    model_ref: ModelRef
    status: GatewayResultStatus = GatewayResultStatus.SUCCESS
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    usage: Optional[UsageResult] = None
    error_message: Optional[str] = None
    is_parse_retry: bool = False
    operation_ref: Optional["OperationRef"] = None


@dataclass(frozen=True)
class CostAllocation:
    """
    Payer allocation share for a cost event (P06.02 / R14, R31).
    """
    payer_id: Optional[str]
    share_ratio: Decimal = Decimal("1.0")
    allocated_cost_usd: Decimal = Decimal("0.0")
    allocated_cost_toman: Decimal = Decimal("0.0")
    charge_factor: Decimal = Decimal("1.0")


@dataclass
class GatewayResult:
    """
    Comprehensive result of an LLM Gateway execution.
    """
    status: GatewayResultStatus
    content: Optional[str] = None
    parsed_json: Optional[Any] = None
    usage: Optional[UsageResult] = None
    model_ref: Optional[ModelRef] = None
    attempts: List[AttemptRef] = field(default_factory=list)
    error_message: Optional[str] = None
    is_cached: bool = False
    reservation_id: Optional[str] = None


# =====================================================================
# Port Specifications (Interfaces)
# =====================================================================

class BudgetPort(abc.ABC):
    """
    Abstract interface for tenant credit reservation and settlement (R31, R32).
    Enforces Product Decision #2: Insufficient credit must queue, not create debt or bypass.
    """

    @abc.abstractmethod
    def reserve(self, operation: OperationRef, estimated_toman: Decimal) -> Tuple[bool, str, Optional[str]]:
        """
        Attempts to reserve estimated funds for an operation.
        Returns: (is_allowed, reason, reservation_id)
        When balance is insufficient, is_allowed MUST be False and reason="INSUFFICIENT_FUNDS".
        """
        pass

    @abc.abstractmethod
    def settle(self, reservation_id: str, actual_toman: Decimal) -> bool:
        """
        Debits the actual consumed cost and releases any remaining reserved funds.
        """
        pass

    @abc.abstractmethod
    def release(self, reservation_id: str) -> bool:
        """
        Releases reserved funds in case of cancellation or unbilled zero-cost attempt.
        """
        pass

    @abc.abstractmethod
    def reconcile(self, reservation_id: str) -> str:
        """
        Inspects un-settled reservation status for manual or automated recovery.
        """
        pass


class QuotaPort(abc.ABC):
    """
    Abstract interface for cross-worker provider rate-limiting and quota tracking (R15).
    """

    @abc.abstractmethod
    def reserve_provider_quota(self, model_ref: ModelRef, est_tokens: int) -> Tuple[bool, str]:
        """
        Checks and atomically reserves RPM/TPM/RPD for the model/provider.
        Returns: (is_available, reason)
        """
        pass

    @abc.abstractmethod
    def record_provider_success(self, model_ref: ModelRef, actual_tokens: int) -> None:
        """
        Adjusts sliding window upon successful completion.
        """
        pass

    @abc.abstractmethod
    def record_provider_429(self, model_ref: ModelRef, retry_after_sec: Optional[float], is_daily: bool) -> None:
        """
        Engages backoff / cooldown for the given provider/model.
        """
        pass


class LedgerPort(abc.ABC):
    """
    Abstract interface for immutable append-only accounting of LLM executions.
    """

    @abc.abstractmethod
    def record_attempt(
        self,
        operation: OperationRef,
        attempt: AttemptRef,
        price_snapshot: PriceSnapshot,
        ref: str,
    ) -> str:
        """
        Persists a ledger entry for the attempt.
        """
        pass

    def record_cost_event(
        self,
        attempt: AttemptRef,
        price_snapshot: PriceSnapshot,
        usage: UsageResult,
        allocations: List[CostAllocation],
    ) -> Any:
        """
        Atomically records an attempt cost event and its payer allocations (P06.02).
        """
        pass


# =====================================================================
# In-Memory Test Fakes for Phase 5
# =====================================================================

class FakeBudgetPort(BudgetPort):
    """
    In-memory budget port for deterministic unit and integration tests.
    """

    def __init__(self, initial_balances: Optional[Dict[str, Decimal]] = None):
        self.balances: Dict[str, Decimal] = initial_balances or {}
        self.reservations: Dict[str, Dict[str, Any]] = {}
        self.settled: Dict[str, Decimal] = {}
        self.released: List[str] = []

    def set_balance(self, tenant_id: str, amount_toman: Decimal) -> None:
        self.balances[tenant_id] = amount_toman

    def reserve(self, operation: OperationRef, estimated_toman: Decimal) -> Tuple[bool, str, Optional[str]]:
        current_balance = self.balances.get(operation.payer_tenant_id, Decimal("0.0"))
        if current_balance < estimated_toman:
            return False, "INSUFFICIENT_FUNDS", None

        # Lock funds in reservation
        res_id = f"res_{uuid.uuid4().hex[:12]}"
        self.balances[operation.payer_tenant_id] = current_balance - estimated_toman
        self.reservations[res_id] = {
            "tenant_id": operation.payer_tenant_id,
            "reserved_amount": estimated_toman,
            "status": "RESERVED",
        }
        return True, "RESERVED", res_id

    def settle(self, reservation_id: str, actual_toman: Decimal) -> bool:
        if reservation_id not in self.reservations:
            return False
        res = self.reservations[reservation_id]
        if res["status"] != "RESERVED":
            return False

        tenant_id = res["tenant_id"]
        reserved_amount = res["reserved_amount"]

        # Refund difference if actual < reserved, or debit extra if actual > reserved
        diff = reserved_amount - actual_toman
        self.balances[tenant_id] = self.balances.get(tenant_id, Decimal("0.0")) + diff

        res["status"] = "SETTLED"
        res["actual_toman"] = actual_toman
        self.settled[reservation_id] = actual_toman
        return True

    def release(self, reservation_id: str) -> bool:
        if reservation_id not in self.reservations:
            return False
        res = self.reservations[reservation_id]
        if res["status"] != "RESERVED":
            return False

        tenant_id = res["tenant_id"]
        self.balances[tenant_id] = self.balances.get(tenant_id, Decimal("0.0")) + res["reserved_amount"]
        res["status"] = "RELEASED"
        self.released.append(reservation_id)
        return True

    def reconcile(self, reservation_id: str) -> str:
        return self.reservations.get(reservation_id, {}).get("status", "UNKNOWN")


class FakeQuotaPort(QuotaPort):
    """
    In-memory quota port for tests.
    """

    def __init__(self):
        self.exhausted_models: set[str] = set()
        self.recorded_tokens: Dict[str, int] = {}
        self.cool_until: Dict[str, float] = {}

    def exhaust(self, model_identity: str) -> None:
        self.exhausted_models.add(model_identity.lower())

    def unexhaust(self, model_identity: str) -> None:
        self.exhausted_models.discard(model_identity.lower())

    def reserve_provider_quota(self, model_ref: ModelRef, est_tokens: int) -> Tuple[bool, str]:
        key = model_ref.identity
        if key in self.exhausted_models:
            return False, "DAILY_RPD_EXHAUSTED"
        if key in self.cool_until and time.time() < self.cool_until[key]:
            return False, "COOLDOWN_ACTIVE"
        return True, "OK"

    def record_provider_success(self, model_ref: ModelRef, actual_tokens: int) -> None:
        key = model_ref.identity
        self.recorded_tokens[key] = self.recorded_tokens.get(key, 0) + actual_tokens

    def record_provider_429(self, model_ref: ModelRef, retry_after_sec: Optional[float], is_daily: bool) -> None:
        key = model_ref.identity
        if is_daily:
            self.exhausted_models.add(key)
        else:
            delay = retry_after_sec or 30.0
            self.cool_until[key] = time.time() + delay


class FakeLedgerPort(LedgerPort):
    """
    In-memory ledger port recording attempts.
    """

    def __init__(self):
        self.records: List[Dict[str, Any]] = []

    def record_attempt(
        self,
        operation: OperationRef,
        attempt: AttemptRef,
        price_snapshot: PriceSnapshot,
        ref: str,
    ) -> str:
        record_id = f"led_{uuid.uuid4().hex[:12]}"
        self.records.append({
            "id": record_id,
            "operation_id": operation.operation_id,
            "payer_tenant_id": operation.payer_tenant_id,
            "attempt_number": attempt.attempt_number,
            "model_identity": attempt.model_ref.identity,
            "status": attempt.status.value,
            "cost_usd": attempt.usage.cost_usd if attempt.usage else Decimal("0.0"),
            "cost_toman": attempt.usage.cost_toman if attempt.usage else Decimal("0.0"),
            "is_cached": attempt.usage.is_cached if attempt.usage else False,
            "ref": ref,
        })
        return record_id

    def record_cost_event(
        self,
        attempt: AttemptRef,
        price_snapshot: PriceSnapshot,
        usage: UsageResult,
        allocations: List[CostAllocation],
    ) -> Any:
        record_id = f"evt_{uuid.uuid4().hex[:12]}"
        self.records.append({
            "id": record_id,
            "attempt_id": attempt.attempt_id,
            "operation_id": attempt.operation_id,
            "model_identity": attempt.model_ref.identity,
            "usage": usage,
            "allocations": allocations,
        })
        return record_id
