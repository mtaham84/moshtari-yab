from datetime import timedelta
from django.test import TransactionTestCase
from django.utils import timezone

from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead, OutreachDispatchIntent
from apps.discovery.outreach_recovery import (
    OutreachReconciliationService,
    MockRemoteThreadVerifier,
)
from apps.discovery.outreach_repository import OutreachIntentRepository


class OutreachRecoveryTestCase(TransactionTestCase):
    def setUp(self):
        self.tenant_id = "tenant_rec_p04"
        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_id,
            username="rec.sender",
            role="SENDER",
            status="ACTIVE",
            daily_limit=15,
            daily_action_count=1,
        )
        self.lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            ig_user_pk="ig_pk_7711",
            username="rec_lead",
            comment_text="تست بازیابی کرش",
            status="NEW",
        )

    def test_crashed_sending_intent_transitions_to_unknown_and_is_not_blindly_resent(self):
        """Expired SENDING intent transitions to UNKNOWN and is NOT re-queued to PENDING."""
        past = timezone.now() - timedelta(minutes=15)
        intent = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            sender=self.sender,
            sender_username=self.sender.username,
            recipient_identifier=self.lead.username,
            status="SENDING",
            worker_id="worker_crashed",
            lease_expires_at=past,
            generation=3,
        )

        reclaim_res = OutreachIntentRepository.reclaim_expired_leases()
        self.assertEqual(reclaim_res["transitioned_to_unknown"], 1)

        intent.refresh_from_db()
        self.assertEqual(intent.status, "UNKNOWN")
        self.assertEqual(intent.error_code, "TIMEOUT_CRASH")

        # Calling claim_pending_intent MUST NOT claim an UNKNOWN intent
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="new_worker",
        )
        self.assertIsNone(claimed, "UNKNOWN intents must never be claimed blindly without reconciliation")

    def test_automated_reconciliation_delivers_confirmed_sent(self):
        """Inbox check confirms message was delivered -> intent becomes SENT with remote ID."""
        intent = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            sender=self.sender,
            sender_username=self.sender.username,
            recipient_identifier=self.lead.username,
            message_text="سلام وقت شما بخیر",
            status="UNKNOWN",
            attempt_count=1,
            generation=4,
        )

        mock_verifier = MockRemoteThreadVerifier(delivered=True, remote_id="ig_verified_rec_456")
        res = OutreachReconciliationService.reconcile_intent_automated(
            intent_id=intent.intent_id,
            verifier=mock_verifier,
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["resolution"], "SENT")
        self.assertEqual(res["remote_message_id"], "ig_verified_rec_456")

        intent.refresh_from_db()
        self.assertEqual(intent.status, "SENT")
        self.assertEqual(intent.remote_message_id, "ig_verified_rec_456")

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "DM_SENT")

    def test_automated_reconciliation_undelivered_safely_requeues_or_fails_on_max(self):
        """Inbox check confirms message was NOT delivered -> re-queues to PENDING or FAILED if max attempts."""
        intent = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            sender=self.sender,
            sender_username=self.sender.username,
            recipient_identifier=self.lead.username,
            status="UNKNOWN",
            attempt_count=1,
            max_attempts=3,
            generation=4,
        )

        mock_verifier = MockRemoteThreadVerifier(delivered=False, verified=True)

        # Attempt 1 -> safely re-queued to PENDING
        res = OutreachReconciliationService.reconcile_intent_automated(
            intent_id=intent.intent_id,
            verifier=mock_verifier,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["resolution"], "PENDING")

        intent.refresh_from_db()
        self.assertEqual(intent.status, "PENDING")

        # Now simulate attempt_count reaching max_attempts (3)
        intent.status = "UNKNOWN"
        intent.attempt_count = 3
        intent.save(update_fields=["status", "attempt_count"])

        res_max = OutreachReconciliationService.reconcile_intent_automated(
            intent_id=intent.intent_id,
            verifier=mock_verifier,
        )
        self.assertTrue(res_max["success"])
        self.assertEqual(res_max["resolution"], "FAILED")

        intent.refresh_from_db()
        self.assertEqual(intent.status, "FAILED")

    def test_manual_operator_reconciliation_with_audit_logging(self):
        """Admin manual reconciliation resolves UNKNOWN to SENT or FAILED with security audit (R41)."""
        intent = OutreachDispatchIntent.objects.create(
            tenant_id=self.tenant_id,
            lead=self.lead,
            sender=self.sender,
            sender_username=self.sender.username,
            recipient_identifier=self.lead.username,
            status="UNKNOWN",
            attempt_count=1,
            generation=4,
        )

        with self.assertLogs("security.audit", level="WARNING") as cm:
            res = OutreachReconciliationService.reconcile_intent_manual(
                intent_id=intent.intent_id,
                resolution="SENT",
                admin_username="support_lead_reza",
                reason="چک دستی در اکانت اینستاگرام رها تایید کرد دایرکت ارسال شده است",
                manual_remote_id="manual_ack_8899",
            )
            self.assertTrue(res["success"])
            self.assertTrue(any("OUTREACH_UNKNOWN_MANUAL_RECONCILIATION" in log for log in cm.output))

        intent.refresh_from_db()
        self.assertEqual(intent.status, "SENT")
        self.assertEqual(intent.remote_message_id, "manual_ack_8899")

        # Empty reason rejected
        intent.status = "UNKNOWN"
        intent.save(update_fields=["status"])
        with self.assertRaises(ValueError):
            OutreachReconciliationService.reconcile_intent_manual(
                intent_id=intent.intent_id,
                resolution="SENT",
                admin_username="support_lead_reza",
                reason="",
            )
