"""
Atomic Claim, Lease Ownership, and CAS Repository for Outreach Intents (P04.04 / R11, R12).

Provides concurrency control via SELECT FOR UPDATE SKIP LOCKED,
Optimistic Locking / CAS (Compare-and-Set) on generation + worker_id,
and safe reclamation of expired leases.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import uuid

from django.db import transaction, models
from django.db.models import F
from django.utils import timezone

from apps.discovery.models import OutreachDispatchIntent, InstagramEngineAccount, InstagramEngineLead


class OutreachIntentRepository:
    """
    Repository layer for persistent outreach intents, enforcing short transactions,
    worker lease ownership, and crash-resilient status transitions.
    """

    @classmethod
    def create_intent(
        cls,
        lead: InstagramEngineLead,
        sender: Optional[InstagramEngineAccount] = None,
        recipient_identifier: str = "",
        message_text: str = "",
        message_type: str = "initial",
        tenant_id: Optional[str] = None,
    ) -> OutreachDispatchIntent:
        """
        Creates a new intent in PENDING status.
        Protected by DB constraint uq_active_intent_per_lead_type against duplicate active intents.
        """
        t_id = tenant_id or lead.tenant_id
        recipient = recipient_identifier or lead.username
        sender_user = sender.username if sender else ""

        intent = OutreachDispatchIntent.objects.create(
            tenant_id=t_id,
            lead=lead,
            sender=sender,
            sender_username=sender_user,
            recipient_identifier=recipient,
            message_type=message_type,
            message_text=message_text,
            status="PENDING",
        )
        return intent

    @classmethod
    def claim_pending_intent(
        cls,
        tenant_ids: List[str],
        worker_id: str,
        lease_duration_sec: int = 300,
        now: Optional[datetime] = None,
    ) -> Optional[OutreachDispatchIntent]:
        """
        Atomically claims a single PENDING or expired-CLAIMED intent using
        SELECT FOR UPDATE SKIP LOCKED in a short committed transaction (P04.04).
        Returns None if no eligible intent is available or all are locked by other workers.
        """
        current_time = now or timezone.now()
        lease_until = current_time + timedelta(seconds=lease_duration_sec)

        with transaction.atomic():
            candidates = OutreachDispatchIntent.objects.select_for_update(skip_locked=True).filter(
                models.Q(status="PENDING")
                | models.Q(status="CLAIMED", lease_expires_at__lte=current_time)
            )

            if tenant_ids:
                candidates = candidates.filter(tenant_id__in=tenant_ids)

            intent = candidates.order_by("created_at").first()
            if not intent:
                return None

            # CAS assignment
            OutreachDispatchIntent.objects.filter(pk=intent.pk).update(
                status="CLAIMED",
                worker_id=worker_id,
                lease_expires_at=lease_until,
                generation=F("generation") + 1,
                updated_at=current_time,
            )
            intent.refresh_from_db()
            return intent

    @classmethod
    def compare_and_set_status(
        cls,
        intent_id: str | uuid.UUID,
        expected_generation: int,
        expected_worker_id: str,
        new_status: str,
        new_worker_id: Optional[str] = None,
        lease_expires_at: Optional[datetime] = None,
        remote_message_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_detail: Optional[str] = None,
        increment_attempt: bool = False,
        now: Optional[datetime] = None,
    ) -> bool:
        """
        Performs an atomic Compare-And-Set (CAS) update on the intent row.
        Guarantees that a stale worker whose lease expired or whose generation
        was incremented CANNOT overwrite current state.
        Returns True if exactly 1 row was updated, False otherwise.
        """
        current_time = now or timezone.now()

        update_kwargs: Dict[str, Any] = {
            "status": new_status,
            "worker_id": new_worker_id,
            "lease_expires_at": lease_expires_at,
            "generation": F("generation") + 1,
            "updated_at": current_time,
        }

        if remote_message_id is not None:
            update_kwargs["remote_message_id"] = remote_message_id
        if error_code is not None:
            update_kwargs["error_code"] = error_code
        if error_detail is not None:
            update_kwargs["error_detail"] = error_detail
        if increment_attempt:
            update_kwargs["attempt_count"] = F("attempt_count") + 1

        rows_updated = OutreachDispatchIntent.objects.filter(
            intent_id=intent_id,
            generation=expected_generation,
            worker_id=expected_worker_id,
        ).update(**update_kwargs)

        return rows_updated == 1

    @classmethod
    def reclaim_expired_leases(cls, now: Optional[datetime] = None) -> Dict[str, int]:
        """
        Safely reclaims expired worker leases (P04.04):
        1. Expired CLAIMED intents (which never touched the network) return to PENDING.
        2. Expired SENDING intents (whose network state is in-flight or timed out)
           MUST transition to UNKNOWN, NEVER to PENDING, to avoid duplicate dispatch.
        """
        current_time = now or timezone.now()

        with transaction.atomic():
            # 1. Expired CLAIMED intents -> PENDING
            reclaimed_claimed = OutreachDispatchIntent.objects.filter(
                status="CLAIMED",
                lease_expires_at__lte=current_time,
            ).update(
                status="PENDING",
                worker_id=None,
                lease_expires_at=None,
                generation=F("generation") + 1,
                updated_at=current_time,
            )

            # 2. Expired SENDING intents -> UNKNOWN (Crash / Network Timeout)
            reclaimed_sending = OutreachDispatchIntent.objects.filter(
                status="SENDING",
                lease_expires_at__lte=current_time,
            ).update(
                status="UNKNOWN",
                worker_id=None,
                lease_expires_at=None,
                error_code="TIMEOUT_CRASH",
                error_detail="Transmission lease expired while in SENDING status; marked UNKNOWN for safe reconciliation",
                generation=F("generation") + 1,
                updated_at=current_time,
            )

        return {
            "reclaimed_to_pending": reclaimed_claimed,
            "transitioned_to_unknown": reclaimed_sending,
        }
