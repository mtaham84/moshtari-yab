"""
Outreach Intent Contract & State Machine (P04.02 / R11, R12).

Defines the formal finite state machine (FSM), lease ownership semantics,
attempt/generation tracking, and crash-resilience contract for automated
and human-reviewed outreach messaging.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Dict, Set, Optional, Any
import uuid


class OutreachStatus(str, Enum):
    PENDING = "PENDING"      # Intent created, waiting to be claimed by an outreach worker
    CLAIMED = "CLAIMED"      # Worker has leased the intent for pre-flight qualification/warmup
    SENDING = "SENDING"      # Network transmission in-flight; crash during this phase yields UNKNOWN
    SENT = "SENT"            # Terminal success: message confirmed delivered with remote reference
    FAILED = "FAILED"        # Terminal failure: permanent rejection (e.g. user not found, blocked)
    UNKNOWN = "UNKNOWN"      # Indeterminate state: crash or timeout during transmission; requires reconciliation
    SKIPPED = "SKIPPED"      # Terminal skip: recipient ineligible under account guardrails


class InvalidTransitionError(Exception):
    """Raised when an illegal state machine transition is attempted."""
    pass


class LeaseOwnershipError(Exception):
    """Raised when a worker attempts to modify an intent without holding the active lease."""
    pass


class LeaseExpiredError(Exception):
    """Raised when an operation is attempted on an intent whose lease has expired."""
    pass


# Strict State Transition Matrix
# key: current_status -> value: set of permitted destination statuses
ALLOWED_TRANSITIONS: Dict[OutreachStatus, Set[OutreachStatus]] = {
    OutreachStatus.PENDING: {
        OutreachStatus.CLAIMED,
        OutreachStatus.SKIPPED,
    },
    OutreachStatus.CLAIMED: {
        OutreachStatus.SENDING,
        OutreachStatus.PENDING,   # Expired lease released back to queue before transmission started
        OutreachStatus.SKIPPED,   # Guardrail evaluation determined recipient is ineligible
        OutreachStatus.FAILED,    # Client initialization / pre-flight preparation failed permanently
    },
    OutreachStatus.SENDING: {
        OutreachStatus.SENT,      # Transmission succeeded
        OutreachStatus.FAILED,    # API returned a permanent failure error code
        OutreachStatus.UNKNOWN,   # Network dropped, timeout, worker crash during send call
    },
    OutreachStatus.UNKNOWN: {
        OutreachStatus.SENT,      # Reconciliation verified message was indeed delivered
        OutreachStatus.FAILED,    # Reconciliation verified message was rejected permanently
        OutreachStatus.PENDING,   # Reconciliation verified message was NEVER received; safe to re-queue
    },
    OutreachStatus.SENT: set(),    # Terminal state: strictly immutable
    OutreachStatus.FAILED: set(),  # Terminal state: immutable; follow-ups or retries require a new intent
    OutreachStatus.SKIPPED: set(), # Terminal state: immutable
}


@dataclass
class OutreachIntent:
    intent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = "tenant_default"
    lead_id: str = ""
    recipient_identifier: str = ""
    sender_username: str = ""
    message_type: str = "initial"   # "initial" | "followup"
    message_text: str = ""
    status: OutreachStatus = OutreachStatus.PENDING
    worker_id: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    generation: int = 1
    attempt_count: int = 0
    max_attempts: int = 3
    remote_message_id: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class OutreachStateMachine:
    """
    Validates and applies state transitions on an OutreachIntent according
    to the formal contract.
    """

    @staticmethod
    def can_transition(current_status: OutreachStatus, target_status: OutreachStatus) -> bool:
        return target_status in ALLOWED_TRANSITIONS.get(current_status, set())

    @classmethod
    def claim(
        cls,
        intent: OutreachIntent,
        worker_id: str,
        lease_duration_sec: int = 300,
        now: Optional[datetime] = None,
    ) -> OutreachIntent:
        current_time = now or datetime.now(timezone.utc)

        # If already claimed, check if current lease is valid
        if intent.status == OutreachStatus.CLAIMED:
            if intent.lease_expires_at and intent.lease_expires_at > current_time:
                if intent.worker_id != worker_id:
                    raise LeaseOwnershipError(
                        f"Intent {intent.intent_id} is currently leased by worker {intent.worker_id} until {intent.lease_expires_at}"
                    )
            # Lease has expired, can be re-claimed by new worker

        elif not cls.can_transition(intent.status, OutreachStatus.CLAIMED):
            raise InvalidTransitionError(
                f"Cannot claim intent in state {intent.status}. Permitted from PENDING only."
            )

        intent.status = OutreachStatus.CLAIMED
        intent.worker_id = worker_id
        intent.lease_expires_at = current_time + timedelta(seconds=lease_duration_sec)
        intent.generation += 1
        intent.updated_at = current_time
        return intent

    @classmethod
    def start_sending(
        cls,
        intent: OutreachIntent,
        worker_id: str,
        now: Optional[datetime] = None,
    ) -> OutreachIntent:
        current_time = now or datetime.now(timezone.utc)
        if not cls.can_transition(intent.status, OutreachStatus.SENDING):
            raise InvalidTransitionError(
                f"Cannot start sending intent in state {intent.status}. Must be in CLAIMED."
            )
        cls._verify_lease(intent, worker_id, current_time)

        intent.status = OutreachStatus.SENDING
        intent.attempt_count += 1
        intent.generation += 1
        intent.updated_at = current_time
        return intent

    @classmethod
    def mark_sent(
        cls,
        intent: OutreachIntent,
        worker_id: str,
        remote_message_id: str,
        now: Optional[datetime] = None,
    ) -> OutreachIntent:
        current_time = now or datetime.now(timezone.utc)
        if not cls.can_transition(intent.status, OutreachStatus.SENT):
            raise InvalidTransitionError(
                f"Cannot mark intent as SENT from state {intent.status}."
            )
        cls._verify_lease(intent, worker_id, current_time)

        if not remote_message_id:
            raise ValueError("remote_message_id is mandatory to confirm delivery.")

        intent.status = OutreachStatus.SENT
        intent.remote_message_id = remote_message_id
        intent.worker_id = None
        intent.lease_expires_at = None
        intent.generation += 1
        intent.updated_at = current_time
        return intent

    @classmethod
    def mark_failed(
        cls,
        intent: OutreachIntent,
        worker_id: str,
        error_code: str,
        error_detail: str = "",
        now: Optional[datetime] = None,
    ) -> OutreachIntent:
        current_time = now or datetime.now(timezone.utc)
        if not cls.can_transition(intent.status, OutreachStatus.FAILED):
            raise InvalidTransitionError(
                f"Cannot mark intent as FAILED from state {intent.status}."
            )
        cls._verify_lease(intent, worker_id, current_time)

        intent.status = OutreachStatus.FAILED
        intent.error_code = error_code
        intent.error_detail = error_detail
        intent.worker_id = None
        intent.lease_expires_at = None
        intent.generation += 1
        intent.updated_at = current_time
        return intent

    @classmethod
    def mark_skipped(
        cls,
        intent: OutreachIntent,
        worker_id: Optional[str] = None,
        reason: str = "",
        now: Optional[datetime] = None,
    ) -> OutreachIntent:
        current_time = now or datetime.now(timezone.utc)
        if intent.status == OutreachStatus.CLAIMED and worker_id:
            cls._verify_lease(intent, worker_id, current_time)

        if not cls.can_transition(intent.status, OutreachStatus.SKIPPED):
            raise InvalidTransitionError(
                f"Cannot skip intent in state {intent.status}."
            )

        intent.status = OutreachStatus.SKIPPED
        intent.error_detail = reason
        intent.worker_id = None
        intent.lease_expires_at = None
        intent.generation += 1
        intent.updated_at = current_time
        return intent

    @classmethod
    def mark_unknown_on_crash(
        cls,
        intent: OutreachIntent,
        reason: str = "Worker crash / network timeout during SENDING",
        now: Optional[datetime] = None,
    ) -> OutreachIntent:
        current_time = now or datetime.now(timezone.utc)
        if intent.status != OutreachStatus.SENDING:
            raise InvalidTransitionError(
                f"Only an in-flight SENDING intent can transition to UNKNOWN. Current: {intent.status}"
            )

        intent.status = OutreachStatus.UNKNOWN
        intent.error_code = "CRASH_OR_TIMEOUT"
        intent.error_detail = reason
        intent.worker_id = None
        intent.lease_expires_at = None
        intent.generation += 1
        intent.updated_at = current_time
        return intent

    @classmethod
    def reconcile_unknown(
        cls,
        intent: OutreachIntent,
        resolution: OutreachStatus,
        remote_message_id: Optional[str] = None,
        notes: str = "",
        now: Optional[datetime] = None,
    ) -> OutreachIntent:
        current_time = now or datetime.now(timezone.utc)
        if intent.status != OutreachStatus.UNKNOWN:
            raise InvalidTransitionError(
                f"Can only reconcile an intent currently in UNKNOWN. Current: {intent.status}"
            )

        if resolution == OutreachStatus.SENT:
            if not remote_message_id:
                raise ValueError("remote_message_id required to reconcile as SENT.")
            intent.status = OutreachStatus.SENT
            intent.remote_message_id = remote_message_id
        elif resolution == OutreachStatus.FAILED:
            intent.status = OutreachStatus.FAILED
            intent.error_detail = notes or "Reconciliation confirmed send failure"
        elif resolution == OutreachStatus.PENDING:
            if intent.attempt_count >= intent.max_attempts:
                intent.status = OutreachStatus.FAILED
                intent.error_detail = f"Max attempts ({intent.max_attempts}) reached after UNKNOWN reconciliation"
            else:
                intent.status = OutreachStatus.PENDING
                intent.error_detail = notes or "Reconciliation verified message was not delivered; safe to re-queue"
        else:
            raise InvalidTransitionError(f"Cannot reconcile UNKNOWN to {resolution}")

        intent.worker_id = None
        intent.lease_expires_at = None
        intent.generation += 1
        intent.updated_at = current_time
        return intent

    @classmethod
    def release_expired_claim(
        cls,
        intent: OutreachIntent,
        now: Optional[datetime] = None,
    ) -> OutreachIntent:
        current_time = now or datetime.now(timezone.utc)
        if intent.status != OutreachStatus.CLAIMED:
            raise InvalidTransitionError("Only CLAIMED intents can have leases released.")

        if intent.lease_expires_at and intent.lease_expires_at > current_time:
            raise LeaseOwnershipError("Cannot release a claim whose lease has not yet expired.")

        intent.status = OutreachStatus.PENDING
        intent.worker_id = None
        intent.lease_expires_at = None
        intent.generation += 1
        intent.updated_at = current_time
        return intent

    @staticmethod
    def _verify_lease(intent: OutreachIntent, worker_id: str, current_time: datetime) -> None:
        if intent.worker_id != worker_id:
            raise LeaseOwnershipError(
                f"Worker '{worker_id}' does not hold active lease on intent {intent.intent_id} (held by '{intent.worker_id}')"
            )
        if intent.lease_expires_at and intent.lease_expires_at < current_time:
            raise LeaseExpiredError(
                f"Lease on intent {intent.intent_id} expired at {intent.lease_expires_at} (current time: {current_time})"
            )
