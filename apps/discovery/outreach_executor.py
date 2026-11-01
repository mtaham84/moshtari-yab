"""
Outreach Executor and Client Adapter (P04.06 / R01, R11, R12).

Executes outreach intents under strict concurrency control:
1. Re-validates tenant access policy and sender eligibility PRE-FLIGHT before Client invocation.
2. Performs atomic CAS transition from CLAIMED to SENDING.
3. Stale worker / expired lease aborts before touching the network.
4. Network transmission occurs OUTSIDE database transactions.
5. Distinguishes permanent API failures from indeterminate network crashes (UNKNOWN).
"""

from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Optional, Any, Dict
import uuid

from django.utils import timezone

from apps.core.access_policy import AccessPolicy
from apps.discovery.models import OutreachDispatchIntent, InstagramEngineAccount, InstagramEngineLead
from apps.discovery.outreach_repository import OutreachIntentRepository
from apps.discovery.sender_policy import SenderPolicy

logger = logging.getLogger("instagram_outreach")


@dataclass
class OutreachSendResult:
    success: bool
    remote_message_id: Optional[str] = None
    is_transient: bool = False
    error_code: Optional[str] = None
    error_detail: Optional[str] = None


class BaseOutreachClientAdapter:
    """Abstract interface for messaging client adapters."""
    def send_direct_message(
        self,
        account: InstagramEngineAccount,
        target_username: str,
        target_pk: Optional[str],
        text: str,
    ) -> OutreachSendResult:
        raise NotImplementedError


class MockOutreachClientAdapter(BaseOutreachClientAdapter):
    """
    Test and dry-run adapter providing deterministic simulation of success,
    permanent rejection, and network crash behaviors.
    """
    def __init__(
        self,
        should_succeed: bool = True,
        mock_remote_id: str = "mock_ig_msg_101",
        is_transient: bool = False,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
        simulated_exception: Optional[Exception] = None,
    ):
        self.should_succeed = should_succeed
        self.mock_remote_id = mock_remote_id
        self.is_transient = is_transient
        self.error_code = error_code
        self.error_detail = error_detail
        self.simulated_exception = simulated_exception
        self.calls: list = []

    def send_direct_message(
        self,
        account: InstagramEngineAccount,
        target_username: str,
        target_pk: Optional[str],
        text: str,
    ) -> OutreachSendResult:
        self.calls.append({
            "account": account.username,
            "target": target_username,
            "pk": target_pk,
            "text": text,
        })
        if self.simulated_exception:
            raise self.simulated_exception

        if self.should_succeed:
            return OutreachSendResult(
                success=True,
                remote_message_id=self.mock_remote_id,
            )
        return OutreachSendResult(
            success=False,
            is_transient=self.is_transient,
            error_code=self.error_code or "API_ERROR",
            error_detail=self.error_detail or "Simulated failure",
        )


class OutreachExecutor:
    """
    Coordinates pre-flight validation, CAS state transitions, network invocation,
    and post-transmission state persistence.
    """

    @classmethod
    def execute_intent(
        cls,
        intent_id: str | uuid.UUID,
        worker_id: str,
        client_adapter: Optional[BaseOutreachClientAdapter] = None,
        expected_generation: Optional[int] = None,
        clock: Optional[datetime] = None,
        ignore_circadian: bool = False,
    ) -> Dict[str, Any]:
        """
        Executes an assigned outreach intent according to P04.06 contract rules.
        """
        now = clock or timezone.now()
        adapter = client_adapter or MockOutreachClientAdapter()

        # Step 1: Pre-Flight Fetch and Validation
        try:
            intent = OutreachDispatchIntent.objects.select_related("lead", "sender").get(intent_id=intent_id)
        except OutreachDispatchIntent.DoesNotExist:
            return {"success": False, "reason": "INTENT_NOT_FOUND"}

        if expected_generation is not None and intent.generation != expected_generation:
            return {"success": False, "reason": "STALE_EXECUTOR_CAS_FAILED"}

        if intent.status != "CLAIMED":
            return {"success": False, "reason": f"INVALID_STATUS_{intent.status}"}

        if intent.worker_id != worker_id:
            return {"success": False, "reason": "WORKER_LEASE_MISMATCH"}

        if intent.lease_expires_at and intent.lease_expires_at <= now:
            return {"success": False, "reason": "LEASE_EXPIRED"}

        sender = intent.sender
        if not sender:
            return {"success": False, "reason": "NO_SENDER_ASSIGNED"}

        # Validate tenant boundaries (R01)
        allowed_senders = AccessPolicy.sender_search_tenants(intent.tenant_id)
        if sender.tenant_id not in allowed_senders:
            OutreachIntentRepository.compare_and_set_status(
                intent_id=intent.intent_id,
                expected_generation=intent.generation,
                expected_worker_id=worker_id,
                new_status="FAILED",
                error_code="CROSS_TENANT_VIOLATION",
                error_detail=f"Sender @{sender.username} does not belong to tenant {intent.tenant_id}",
                now=now,
            )
            return {"success": False, "reason": "CROSS_TENANT_VIOLATION"}

        # Validate SenderPolicy eligibility (R12)
        eligible, reason = SenderPolicy.check_eligibility(sender, clock=now, ignore_circadian=ignore_circadian)
        if not eligible:
            # Release claim back to PENDING so another eligible sender can deliver it
            OutreachIntentRepository.compare_and_set_status(
                intent_id=intent.intent_id,
                expected_generation=intent.generation,
                expected_worker_id=worker_id,
                new_status="PENDING",
                error_detail=f"Sender @{sender.username} ineligible: {reason}",
                now=now,
            )
            return {"success": False, "reason": f"SENDER_INELIGIBLE: {reason}"}

        # Step 2: Atomic CAS transition to SENDING before network call
        gen_before_send = intent.generation
        cas_ok = OutreachIntentRepository.compare_and_set_status(
            intent_id=intent.intent_id,
            expected_generation=gen_before_send,
            expected_worker_id=worker_id,
            new_status="SENDING",
            new_worker_id=worker_id,
            increment_attempt=True,
            now=now,
        )
        if not cas_ok:
            # Stale executor detected! Another process or lease sweeper modified the record.
            # ABORT immediately without invoking the client!
            return {"success": False, "reason": "STALE_EXECUTOR_CAS_FAILED"}

        # Reserve Quota atomically
        reserved, res_msg = SenderPolicy.reserve_quota(
            sender_id=sender.id,
            intent_id=str(intent.intent_id),
            clock=now,
            ignore_circadian=ignore_circadian,
        )
        if not reserved:
            # Revert to PENDING if quota reservation fails
            OutreachIntentRepository.compare_and_set_status(
                intent_id=intent.intent_id,
                expected_generation=gen_before_send + 1,
                expected_worker_id=worker_id,
                new_status="PENDING",
                error_detail=f"Quota reservation failed: {res_msg}",
                now=now,
            )
            return {"success": False, "reason": f"QUOTA_EXHAUSTED: {res_msg}"}

        # Step 3: Network Transmission (OUTSIDE database transactions)
        try:
            send_result = adapter.send_direct_message(
                account=sender,
                target_username=intent.recipient_identifier,
                target_pk=intent.lead.ig_user_pk,
                text=intent.message_text,
            )
        except Exception as exc:
            # Ambiguous crash / network failure
            logger.exception("Crash during direct transmission of intent %s: %s", intent.intent_id, exc)
            send_result = OutreachSendResult(
                success=False,
                is_transient=True,
                error_code="NETWORK_CRASH_OR_TIMEOUT",
                error_detail=str(exc),
            )

        # Step 4: Finalize State
        gen_after_send = gen_before_send + 1

        if send_result.success:
            # Confirmed Success -> SENT
            OutreachIntentRepository.compare_and_set_status(
                intent_id=intent.intent_id,
                expected_generation=gen_after_send,
                expected_worker_id=worker_id,
                new_status="SENT",
                remote_message_id=send_result.remote_message_id,
                now=now,
            )
            SenderPolicy.confirm_quota(sender.id, str(intent.intent_id))

            # Sync lead table if initial outreach
            lead = intent.lead
            lead.status = "DM_SENT"
            lead.sender_account = sender.username
            lead.initial_dm_text = intent.message_text
            lead.initial_dm_at = now
            lead.save(update_fields=["status", "sender_account", "initial_dm_text", "initial_dm_at", "updated_at"])

            return {"success": True, "remote_message_id": send_result.remote_message_id}

        elif not send_result.is_transient:
            # Permanent failure -> FAILED
            OutreachIntentRepository.compare_and_set_status(
                intent_id=intent.intent_id,
                expected_generation=gen_after_send,
                expected_worker_id=worker_id,
                new_status="FAILED",
                error_code=send_result.error_code,
                error_detail=send_result.error_detail,
                now=now,
            )
            # Release quota because message was rejected before delivery
            SenderPolicy.release_quota(sender.id, str(intent.intent_id))
            return {"success": False, "reason": "PERMANENT_FAILURE", "code": send_result.error_code}

        else:
            # Indeterminate crash / transient timeout -> UNKNOWN
            OutreachIntentRepository.compare_and_set_status(
                intent_id=intent.intent_id,
                expected_generation=gen_after_send,
                expected_worker_id=worker_id,
                new_status="UNKNOWN",
                error_code=send_result.error_code or "TIMEOUT_CRASH",
                error_detail=send_result.error_detail or "Network timeout during transmission",
                now=now,
            )
            # Do NOT release quota yet (in-flight verification needed in reconciliation)
            return {"success": False, "reason": "INDETERMINATE_UNKNOWN", "code": send_result.error_code}
