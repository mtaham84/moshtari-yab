from datetime import datetime, timezone, timedelta
import unittest
from apps.discovery.outreach_contract import (
    OutreachStatus,
    OutreachIntent,
    OutreachStateMachine,
    InvalidTransitionError,
    LeaseOwnershipError,
    LeaseExpiredError,
)


class OutreachIntentContractTestCase(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc)
        self.intent = OutreachIntent(
            intent_id="intent-abc-123",
            tenant_id="tenant_4",
            lead_id="lead-001",
            recipient_identifier="target_user",
            sender_username="raha.rostamim",
            message_text="سلام وقت بخیر",
        )

    def test_happy_path_lifecycle(self):
        """PENDING -> CLAIMED -> SENDING -> SENT with generation & attempt tracking."""
        self.assertEqual(self.intent.status, OutreachStatus.PENDING)
        self.assertEqual(self.intent.generation, 1)
        self.assertEqual(self.intent.attempt_count, 0)

        # 1. Claim by Worker A
        OutreachStateMachine.claim(self.intent, worker_id="worker_A", lease_duration_sec=300, now=self.now)
        self.assertEqual(self.intent.status, OutreachStatus.CLAIMED)
        self.assertEqual(self.intent.worker_id, "worker_A")
        self.assertEqual(self.intent.lease_expires_at, self.now + timedelta(seconds=300))
        self.assertEqual(self.intent.generation, 2)

        # 2. Start Sending
        send_time = self.now + timedelta(seconds=10)
        OutreachStateMachine.start_sending(self.intent, worker_id="worker_A", now=send_time)
        self.assertEqual(self.intent.status, OutreachStatus.SENDING)
        self.assertEqual(self.intent.attempt_count, 1)
        self.assertEqual(self.intent.generation, 3)

        # 3. Mark Sent with remote ID
        confirm_time = self.now + timedelta(seconds=25)
        OutreachStateMachine.mark_sent(self.intent, worker_id="worker_A", remote_message_id="ig_msg_998877", now=confirm_time)
        self.assertEqual(self.intent.status, OutreachStatus.SENT)
        self.assertEqual(self.intent.remote_message_id, "ig_msg_998877")
        self.assertIsNone(self.intent.worker_id)
        self.assertIsNone(self.intent.lease_expires_at)
        self.assertEqual(self.intent.generation, 4)

    def test_forbidden_transitions_raise_invalid_transition_error(self):
        """Illegal state transitions must be strictly rejected."""
        # Cannot jump PENDING -> SENT
        with self.assertRaises(InvalidTransitionError):
            OutreachStateMachine.mark_sent(self.intent, worker_id="w1", remote_message_id="msg_1", now=self.now)

        # Cannot jump PENDING -> SENDING
        with self.assertRaises(InvalidTransitionError):
            OutreachStateMachine.start_sending(self.intent, worker_id="w1", now=self.now)

        # Claim intent first
        OutreachStateMachine.claim(self.intent, worker_id="w1", now=self.now)

        # Cannot jump CLAIMED -> SENT directly without SENDING
        with self.assertRaises(InvalidTransitionError):
            OutreachStateMachine.mark_sent(self.intent, worker_id="w1", remote_message_id="msg_1", now=self.now)

        # Move to SENDING
        OutreachStateMachine.start_sending(self.intent, worker_id="w1", now=self.now)

        # Cannot jump SENDING -> PENDING directly (must go to UNKNOWN on crash)
        with self.assertRaises(InvalidTransitionError):
            OutreachStateMachine.claim(self.intent, worker_id="w2", now=self.now)

    def test_terminal_states_are_immutable(self):
        """SENT, FAILED, and SKIPPED cannot transition to any other status."""
        # 1. Terminal SENT
        OutreachStateMachine.claim(self.intent, worker_id="w1", now=self.now)
        OutreachStateMachine.start_sending(self.intent, worker_id="w1", now=self.now)
        OutreachStateMachine.mark_sent(self.intent, worker_id="w1", remote_message_id="msg_1", now=self.now)
        self.assertEqual(self.intent.status, OutreachStatus.SENT)

        with self.assertRaises(InvalidTransitionError):
            OutreachStateMachine.claim(self.intent, worker_id="w2", now=self.now)
        with self.assertRaises(InvalidTransitionError):
            OutreachStateMachine.mark_failed(self.intent, worker_id="w1", error_code="ERR")

        # 2. Terminal SKIPPED
        skipped_intent = OutreachIntent(status=OutreachStatus.PENDING)
        OutreachStateMachine.mark_skipped(skipped_intent, reason="Private account", now=self.now)
        self.assertEqual(skipped_intent.status, OutreachStatus.SKIPPED)

        with self.assertRaises(InvalidTransitionError):
            OutreachStateMachine.claim(skipped_intent, worker_id="w1", now=self.now)

    def test_lease_ownership_enforcement(self):
        """Worker B cannot operate on an intent currently leased to Worker A."""
        OutreachStateMachine.claim(self.intent, worker_id="worker_A", lease_duration_sec=300, now=self.now)

        # Worker B tries to send it
        with self.assertRaises(LeaseOwnershipError):
            OutreachStateMachine.start_sending(self.intent, worker_id="worker_B", now=self.now)

        # Worker B tries to claim it while lease is active
        with self.assertRaises(LeaseOwnershipError):
            OutreachStateMachine.claim(self.intent, worker_id="worker_B", now=self.now)

    def test_lease_expiration_and_reclaim(self):
        """An expired lease blocks the old worker and allows a new worker to claim."""
        OutreachStateMachine.claim(self.intent, worker_id="worker_A", lease_duration_sec=60, now=self.now)

        after_expiration = self.now + timedelta(seconds=70)

        # Worker A tries to send after its lease expired
        with self.assertRaises(LeaseExpiredError):
            OutreachStateMachine.start_sending(self.intent, worker_id="worker_A", now=after_expiration)

        # Worker B can now claim the expired intent
        OutreachStateMachine.claim(self.intent, worker_id="worker_B", lease_duration_sec=120, now=after_expiration)
        self.assertEqual(self.intent.worker_id, "worker_B")
        self.assertEqual(self.intent.lease_expires_at, after_expiration + timedelta(seconds=120))

    def test_crash_in_sending_transitions_to_unknown_and_reconciles(self):
        """
        Crash during transmission sets UNKNOWN.
        Reconciliation resolves to SENT, FAILED, or safe PENDING retry.
        """
        OutreachStateMachine.claim(self.intent, worker_id="worker_A", now=self.now)
        OutreachStateMachine.start_sending(self.intent, worker_id="worker_A", now=self.now)

        # Simulated crash / timeout
        OutreachStateMachine.mark_unknown_on_crash(self.intent, reason="Network connection reset by peer", now=self.now)
        self.assertEqual(self.intent.status, OutreachStatus.UNKNOWN)
        self.assertEqual(self.intent.error_code, "CRASH_OR_TIMEOUT")
        self.assertIsNone(self.intent.worker_id)

        # Reconciliation Case 1: Message was actually delivered
        reconciled_sent = OutreachIntent(**self.intent.__dict__)
        OutreachStateMachine.reconcile_unknown(
            reconciled_sent,
            resolution=OutreachStatus.SENT,
            remote_message_id="verified_ig_123",
            now=self.now,
        )
        self.assertEqual(reconciled_sent.status, OutreachStatus.SENT)
        self.assertEqual(reconciled_sent.remote_message_id, "verified_ig_123")

        # Reconciliation Case 2: Message was NOT delivered, safe to re-queue
        reconciled_retry = OutreachIntent(**self.intent.__dict__)
        OutreachStateMachine.reconcile_unknown(
            reconciled_retry,
            resolution=OutreachStatus.PENDING,
            notes="Inbox check confirmed no outgoing DM sent",
            now=self.now,
        )
        self.assertEqual(reconciled_retry.status, OutreachStatus.PENDING)
        self.assertEqual(reconciled_retry.attempt_count, 1)

        # Reconciliation Case 3: Reached max attempts
        reconciled_max = OutreachIntent(**self.intent.__dict__)
        reconciled_max.attempt_count = 3
        reconciled_max.max_attempts = 3
        OutreachStateMachine.reconcile_unknown(
            reconciled_max,
            resolution=OutreachStatus.PENDING,
            now=self.now,
        )
        self.assertEqual(reconciled_max.status, OutreachStatus.FAILED)
        self.assertIn("Max attempts", reconciled_max.error_detail)
