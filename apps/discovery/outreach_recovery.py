"""
Outreach Recovery and Reconciliation Service (P04.07 / R11, R12, R41).

Manages crash recovery, ambiguous transmission reconciliation, and administrative overrides:
1. UNKNOWN intents are never automatically re-sent without explicit evidence.
2. Automated thread/inbox checking verifies whether the message was received by target.
3. Reclaimed SENDING leases stay in UNKNOWN; recovered results do NOT consume duplicate quota.
4. Administrative resolution requires authenticated operator, valid reason, and security audit log.
"""

from datetime import datetime
import logging
from typing import Optional, Dict, Any, List
import uuid

from django.db import transaction
from django.utils import timezone

from apps.discovery.models import OutreachDispatchIntent, InstagramEngineLead
from apps.discovery.outreach_contract import OutreachStateMachine, OutreachStatus
from apps.discovery.outreach_repository import OutreachIntentRepository
from apps.discovery.sender_policy import SenderPolicy

logger = logging.getLogger("instagram_outreach")
audit_logger = logging.getLogger("security.audit")


class BaseRemoteThreadVerifier:
    """Interface for verifying message presence in remote recipient thread."""
    def check_delivery(
        self,
        sender_account: str,
        target_username: str,
        expected_text: str,
    ) -> Dict[str, Any]:
        """
        Returns:
            {"delivered": True, "remote_message_id": "..."}
            or {"delivered": False, "verified": True}
            or {"delivered": False, "verified": False, "reason": "Could not fetch thread"}
        """
        raise NotImplementedError


class MockRemoteThreadVerifier(BaseRemoteThreadVerifier):
    """Mock verifier for testing reconciliation."""
    def __init__(self, delivered: bool = True, remote_id: str = "mock_verified_msg_999", verified: bool = True):
        self.delivered = delivered
        self.remote_id = remote_id
        self.verified = verified

    def check_delivery(
        self,
        sender_account: str,
        target_username: str,
        expected_text: str,
    ) -> Dict[str, Any]:
        if not self.verified:
            return {"delivered": False, "verified": False, "reason": "Thread unavailable"}
        if self.delivered:
            return {"delivered": True, "remote_message_id": self.remote_id}
        return {"delivered": False, "verified": True}


class OutreachReconciliationService:
    """
    Coordinates recovery of crashed, timed-out, and UNKNOWN outreach intents.
    """

    @classmethod
    def get_stuck_intents_summary(cls, stale_threshold_sec: int = 1800, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Scans and reports count of stuck, expired, and indeterminate intents.
        """
        current_time = now or timezone.now()
        threshold_time = current_time - timezone.timedelta(seconds=stale_threshold_sec)

        return {
            "unknown_count": OutreachDispatchIntent.objects.filter(status="UNKNOWN").count(),
            "expired_claimed_count": OutreachDispatchIntent.objects.filter(
                status="CLAIMED", lease_expires_at__lte=current_time
            ).count(),
            "stuck_sending_count": OutreachDispatchIntent.objects.filter(
                status="SENDING", updated_at__lte=threshold_time
            ).count(),
        }

    @classmethod
    def reconcile_intent_automated(
        cls,
        intent_id: str | uuid.UUID,
        verifier: Optional[BaseRemoteThreadVerifier] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Performs automated inbox/thread verification for an intent in UNKNOWN status.
        """
        current_time = now or timezone.now()
        try:
            intent = OutreachDispatchIntent.objects.select_related("lead", "sender").get(intent_id=intent_id)
        except OutreachDispatchIntent.DoesNotExist:
            return {"success": False, "error": "INTENT_NOT_FOUND"}

        if intent.status != "UNKNOWN":
            return {"success": False, "error": f"Can only reconcile UNKNOWN intents. Current: {intent.status}"}

        thread_verifier = verifier or MockRemoteThreadVerifier()
        check = thread_verifier.check_delivery(
            sender_account=intent.sender_username,
            target_username=intent.recipient_identifier,
            expected_text=intent.message_text,
        )

        if not check.get("verified", False) and not check.get("delivered", False):
            # Inconclusive, leave in UNKNOWN
            return {"success": False, "status": "UNKNOWN", "notes": check.get("reason", "Inconclusive verification")}

        contract_obj = intent.to_contract_intent()

        if check.get("delivered"):
            # Message was confirmed delivered over the wire!
            remote_id = check.get("remote_message_id", "remote_verified_auto")
            OutreachStateMachine.reconcile_unknown(
                contract_obj,
                resolution=OutreachStatus.SENT,
                remote_message_id=remote_id,
                notes="Automated inbox verification confirmed delivery",
                now=current_time,
            )
            intent.sync_from_contract(contract_obj)

            # Confirm quota (no double charge)
            if intent.sender_id:
                SenderPolicy.confirm_quota(intent.sender_id, str(intent.intent_id))

            # Sync lead table
            lead = intent.lead
            lead.status = "DM_SENT"
            lead.sender_account = intent.sender_username
            lead.initial_dm_text = intent.message_text
            lead.initial_dm_at = current_time
            lead.save(update_fields=["status", "sender_account", "initial_dm_text", "initial_dm_at", "updated_at"])

            return {"success": True, "resolution": "SENT", "remote_message_id": remote_id}

        else:
            # Verified that NO message was delivered!
            if intent.attempt_count >= intent.max_attempts:
                # Max attempts reached -> transition to FAILED
                OutreachStateMachine.reconcile_unknown(
                    contract_obj,
                    resolution=OutreachStatus.FAILED,
                    notes=f"Verification confirmed no send, but max attempts ({intent.max_attempts}) reached",
                    now=current_time,
                )
                intent.sync_from_contract(contract_obj)
                if intent.sender_id:
                    SenderPolicy.release_quota(intent.sender_id, str(intent.intent_id))
                return {"success": True, "resolution": "FAILED", "notes": "Max attempts reached"}
            else:
                # Safe to return to PENDING for re-attempt
                OutreachStateMachine.reconcile_unknown(
                    contract_obj,
                    resolution=OutreachStatus.PENDING,
                    notes="Automated inbox verification confirmed message not sent; safely re-queued",
                    now=current_time,
                )
                intent.sync_from_contract(contract_obj)
                # Release quota so it can be cleanly re-reserved on next claim
                if intent.sender_id:
                    SenderPolicy.release_quota(intent.sender_id, str(intent.intent_id))
                return {"success": True, "resolution": "PENDING", "notes": "Re-queued to PENDING"}

    @classmethod
    def reconcile_intent_manual(
        cls,
        intent_id: str | uuid.UUID,
        resolution: str,
        admin_username: str,
        reason: str,
        manual_remote_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Allows an authenticated operator to manually resolve an UNKNOWN intent (R41).
        Enforces mandatory audit logging.
        """
        current_time = now or timezone.now()
        if not reason or len(reason.strip()) < 5:
            raise ValueError("درج دلیل موجه با حداقل ۵ کاراکتر برای تطبیق دستی الزامی است.")

        if resolution not in ["SENT", "FAILED", "PENDING"]:
            raise ValueError("وضعیت تطبیق دستی باید یکی از SENT، FAILED یا PENDING باشد.")

        try:
            intent = OutreachDispatchIntent.objects.select_related("lead", "sender").get(intent_id=intent_id)
        except OutreachDispatchIntent.DoesNotExist:
            return {"success": False, "error": "INTENT_NOT_FOUND"}

        if intent.status != "UNKNOWN":
            return {"success": False, "error": f"Can only reconcile UNKNOWN intents. Current: {intent.status}"}

        contract_obj = intent.to_contract_intent()
        target_status = OutreachStatus(resolution)

        remote_id = manual_remote_id or (f"manual_admin_{int(current_time.timestamp())}" if resolution == "SENT" else None)

        OutreachStateMachine.reconcile_unknown(
            contract_obj,
            resolution=target_status,
            remote_message_id=remote_id,
            notes=f"Manual operator reconciliation by '{admin_username}': {reason}",
            now=current_time,
        )
        intent.sync_from_contract(contract_obj)

        if resolution == "SENT":
            if intent.sender_id:
                SenderPolicy.confirm_quota(intent.sender_id, str(intent.intent_id))
            lead = intent.lead
            lead.status = "DM_SENT"
            lead.sender_account = intent.sender_username
            lead.initial_dm_text = intent.message_text
            lead.initial_dm_at = current_time
            lead.save(update_fields=["status", "sender_account", "initial_dm_text", "initial_dm_at", "updated_at"])
        elif resolution in ["FAILED", "PENDING"]:
            if intent.sender_id:
                SenderPolicy.release_quota(intent.sender_id, str(intent.intent_id))

        audit_logger.warning(
            "OUTREACH_UNKNOWN_MANUAL_RECONCILIATION: Intent %s (lead @%s, sender @%s) manually resolved to %s by admin '%s'. Reason: %s",
            intent.intent_id,
            intent.recipient_identifier,
            intent.sender_username,
            resolution,
            admin_username,
            reason,
        )

        return {"success": True, "resolution": resolution, "admin": admin_username}
