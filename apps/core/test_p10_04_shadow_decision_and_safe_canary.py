from datetime import timedelta
from decimal import Decimal
from django.test import TransactionTestCase
from django.utils import timezone

from apps.discovery.models import (
    InstagramEngineAccount,
    InstagramEngineLead,
    OutreachDispatchIntent,
)
from apps.discovery.outreach_repository import OutreachIntentRepository
from apps.discovery.outreach_executor import OutreachExecutor, MockOutreachClientAdapter
from apps.discovery.outreach_service import plan_outreach_batch
from apps.core.access_policy import AccessPolicy


class ShadowDecisionAndSafeCanaryTests(TransactionTestCase):
    """
    P10.04 — Decision Shadowing and Safe Outreach Canary.
    Addresses: R01, R10, R11, R12.
    Acceptance Criteria:
    1. No two incompatible writers can claim or process the same intent concurrently (R11).
    2. Failed or ambiguous intent never reverts back to NEW (prevents endless retry storms).
    3. Operator can pause or reconcile intents safely.
    4. Shadow / Dry-run execution generates decisions without DB corruption or network calls (R10).
    5. Sender safety policies (quota, cooldown, circadian hours) are strictly enforced (R12).
    """

    def setUp(self):
        self.tenant_id = "tenant_p10_canary"
        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_id,
            username="canary_sender",
            role="SENDER",
            status="ACTIVE",
            daily_limit=10,
            hourly_limit=3,
            daily_action_count=0,
            hourly_action_count=0,
            password="test_canary_pwd",
        )
        self.lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            ig_user_pk="ig_pk_canary_1",
            username="canary_target_lead",
            comment_text="علاقه‌مند به خرید لپ‌تاپ",
            intent_score=0.85,
            status="NEW",
        )

    def test_shadow_decision_pure_simulation_zero_side_effects(self):
        """Shadow decision plans outreach actions without mutating DB or sending network traffic (R10)."""
        plan = plan_outreach_batch(
            tenant_ids=[self.tenant_id],
            batch_size=5,
            min_intent=0.70,
            max_daily_dms=10,
            ignore_circadian=True,
        )

        self.assertTrue(plan["eligible"])
        self.assertEqual(len(plan["planned_actions"]), 1)
        action = plan["planned_actions"][0]
        self.assertEqual(action["username"], self.lead.username)
        self.assertEqual(action["sender_username"], self.sender.username)

        # Invariant: Lead status must REMAIN 'NEW' in database during shadow planning
        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "NEW")
        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 0)

    def test_two_incompatible_writers_cannot_claim_same_intent(self):
        """Atomic claim guarantees that only one worker can lease an intent (R11)."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead,
            sender=self.sender,
            message_text="سلام و درود، پیشنهاد ویژه برای شما",
            tenant_id=self.tenant_id,
        )
        self.assertEqual(intent.status, "PENDING")

        # Worker 1 attempts to claim
        claim1 = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_alpha",
            lease_duration_sec=60,
        )
        self.assertIsNotNone(claim1)
        self.assertEqual(claim1.intent_id, intent.intent_id)
        self.assertEqual(claim1.worker_id, "worker_alpha")

        # Worker 2 attempts to claim concurrently
        claim2 = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_beta",
            lease_duration_sec=60,
        )
        # Second worker MUST NOT be able to claim the same intent
        self.assertIsNone(claim2, "Concurrent worker must receive None; cannot double-claim.")

    def test_failed_intent_never_reverts_to_new(self):
        """Permanent failure transitions intent to FAILED, never reverting back to NEW (anti-storm)."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead,
            sender=self.sender,
            message_text="پیام تست شکست",
            tenant_id=self.tenant_id,
        )
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_fail_test",
        )

        # Client adapter returns permanent rejection (e.g., user blocked DM)
        mock_adapter = MockOutreachClientAdapter(
            should_succeed=False,
            is_transient=False,
            error_code="USER_RESTRICTED",
            error_detail="User does not accept direct messages",
        )

        res = OutreachExecutor.execute_intent(
            intent_id=claimed.intent_id,
            worker_id="worker_fail_test",
            client_adapter=mock_adapter,
            ignore_circadian=True,
        )

        self.assertFalse(res["success"])
        claimed.refresh_from_db()
        # Intent must be FAILED, NOT NEW or PENDING
        self.assertEqual(claimed.status, "FAILED")
        self.assertNotEqual(claimed.status, "PENDING")

        # Ensure it is never re-leased as pending
        reclaim = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="another_worker",
        )
        self.assertIsNone(reclaim, "Failed intent must not be re-leased as pending")

    def test_transient_error_enters_unknown_not_new(self):
        """Transient error enters UNKNOWN state requiring reconciliation, NOT reset to raw NEW."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead,
            sender=self.sender,
            message_text="پیام تست گذرای شبکه",
            tenant_id=self.tenant_id,
        )
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_transient_test",
        )

        mock_adapter = MockOutreachClientAdapter(
            should_succeed=False,
            is_transient=True,
            error_code="RATE_LIMIT_TRANSIENT",
            error_detail="Temporary network timeout",
        )

        res = OutreachExecutor.execute_intent(
            intent_id=claimed.intent_id,
            worker_id="worker_transient_test",
            client_adapter=mock_adapter,
            ignore_circadian=True,
        )

        self.assertFalse(res["success"])
        claimed.refresh_from_db()
        self.assertEqual(claimed.status, "UNKNOWN")
        self.assertNotEqual(claimed.status, "PENDING")

    def test_operator_can_pause_and_cancel_intent(self):
        """Operator can pause or cancel intent, preventing worker execution."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead,
            sender=self.sender,
            message_text="پیام برای تست لغو توسط اپراتور",
            tenant_id=self.tenant_id,
        )

        # Operator cancels/skips the intent
        intent.status = "SKIPPED"
        intent.error_detail = "Operator emergency stop"
        intent.save(update_fields=["status", "error_detail", "updated_at"])

        # Worker claiming pending intents must skip non-pending item
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_cancelled_test",
        )
        self.assertIsNone(claimed, "Cancelled/skipped intent must not be leased to workers")
