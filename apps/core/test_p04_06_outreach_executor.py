from datetime import timedelta
from django.test import TransactionTestCase
from django.utils import timezone

from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead, OutreachDispatchIntent
from apps.discovery.outreach_repository import OutreachIntentRepository
from apps.discovery.outreach_executor import OutreachExecutor, MockOutreachClientAdapter


class OutreachExecutorTestCase(TransactionTestCase):
    def setUp(self):
        self.tenant_id = "tenant_exec_p04"
        self.sender = InstagramEngineAccount.objects.create(
            tenant_id=self.tenant_id,
            username="exec.sender",
            role="SENDER",
            status="ACTIVE",
            daily_limit=20,
            hourly_limit=5,
            daily_action_count=0,
            hourly_action_count=0,
        )
        self.lead = InstagramEngineLead.objects.create(
            tenant_id=self.tenant_id,
            ig_user_pk="ig_pk_8888",
            username="exec_lead",
            comment_text="تست اجرای ارسال",
            status="NEW",
        )

    def test_preflight_tenant_violation_aborts_before_client_call(self):
        """Cross-tenant sender is rejected pre-flight without invoking client adapter (R01)."""
        foreign_sender = InstagramEngineAccount.objects.create(
            tenant_id="foreign_tenant_999",
            username="foreign.sender",
            role="SENDER",
            status="ACTIVE",
        )
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead,
            sender=foreign_sender,
            message_text="سلام",
            tenant_id=self.tenant_id,
        )
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_001",
        )

        mock_adapter = MockOutreachClientAdapter()
        res = OutreachExecutor.execute_intent(
            intent_id=claimed.intent_id,
            worker_id="worker_001",
            client_adapter=mock_adapter,
            ignore_circadian=True,
        )

        self.assertFalse(res["success"])
        self.assertEqual(res["reason"], "CROSS_TENANT_VIOLATION")
        self.assertEqual(len(mock_adapter.calls), 0, "Client adapter must NOT be called on preflight auth failure")

        claimed.refresh_from_db()
        self.assertEqual(claimed.status, "FAILED")
        self.assertEqual(claimed.error_code, "CROSS_TENANT_VIOLATION")

    def test_stale_executor_cas_fails_and_aborts_before_network(self):
        """If worker loses lease or generation advances, execution aborts before calling network."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead,
            sender=self.sender,
            message_text="تست stale executor",
        )
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_A",
        )

        # External change simulates lease theft or concurrent update advancing generation
        OutreachDispatchIntent.objects.filter(pk=claimed.pk).update(generation=99)

        mock_adapter = MockOutreachClientAdapter()
        res = OutreachExecutor.execute_intent(
            intent_id=claimed.intent_id,
            worker_id="worker_A",
            expected_generation=claimed.generation,
            client_adapter=mock_adapter,
            ignore_circadian=True,
        )

        self.assertFalse(res["success"])
        self.assertEqual(res["reason"], "STALE_EXECUTOR_CAS_FAILED")
        self.assertEqual(len(mock_adapter.calls), 0, "Network adapter must NOT be touched by stale executor")

    def test_successful_execution_confirms_sent_and_updates_lead(self):
        """Confirmed send transitions to SENT, records remote ID, and marks lead DM_SENT."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead,
            sender=self.sender,
            message_text="سلام کاتالوگ خدمت شما",
        )
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_live",
        )

        mock_adapter = MockOutreachClientAdapter(should_succeed=True, mock_remote_id="ig_success_7788")
        res = OutreachExecutor.execute_intent(
            intent_id=claimed.intent_id,
            worker_id="worker_live",
            client_adapter=mock_adapter,
            ignore_circadian=True,
        )

        self.assertTrue(res["success"])
        self.assertEqual(res["remote_message_id"], "ig_success_7788")
        self.assertEqual(len(mock_adapter.calls), 1)

        claimed.refresh_from_db()
        self.assertEqual(claimed.status, "SENT")
        self.assertEqual(claimed.remote_message_id, "ig_success_7788")

        self.lead.refresh_from_db()
        self.assertEqual(self.lead.status, "DM_SENT")
        self.assertEqual(self.lead.sender_account, "exec.sender")

    def test_permanent_api_failure_transitions_to_failed_and_releases_quota(self):
        """Permanent rejection (e.g. USER_NOT_FOUND) transitions to FAILED and releases quota."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead,
            sender=self.sender,
            message_text="سلام",
        )
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_live",
        )

        mock_adapter = MockOutreachClientAdapter(
            should_succeed=False,
            is_transient=False,
            error_code="USER_NOT_FOUND",
            error_detail="Target username does not exist on Instagram",
        )
        res = OutreachExecutor.execute_intent(
            intent_id=claimed.intent_id,
            worker_id="worker_live",
            client_adapter=mock_adapter,
            ignore_circadian=True,
        )

        self.assertFalse(res["success"])
        self.assertEqual(res["reason"], "PERMANENT_FAILURE")
        self.assertEqual(res["code"], "USER_NOT_FOUND")

        claimed.refresh_from_db()
        self.assertEqual(claimed.status, "FAILED")
        self.assertEqual(claimed.error_code, "USER_NOT_FOUND")

        # Quota should have been released back to 0
        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 0)

    def test_network_crash_or_timeout_transitions_to_unknown(self):
        """In-flight socket crash or timeout transitions to UNKNOWN (never retried blindly)."""
        intent = OutreachIntentRepository.create_intent(
            lead=self.lead,
            sender=self.sender,
            message_text="سلام",
        )
        claimed = OutreachIntentRepository.claim_pending_intent(
            tenant_ids=[self.tenant_id],
            worker_id="worker_live",
        )

        mock_adapter = MockOutreachClientAdapter(
            simulated_exception=TimeoutError("Connection timed out waiting for Instagram Direct ACK")
        )
        res = OutreachExecutor.execute_intent(
            intent_id=claimed.intent_id,
            worker_id="worker_live",
            client_adapter=mock_adapter,
            ignore_circadian=True,
        )

        self.assertFalse(res["success"])
        self.assertEqual(res["reason"], "INDETERMINATE_UNKNOWN")

        claimed.refresh_from_db()
        self.assertEqual(claimed.status, "UNKNOWN")
        self.assertEqual(claimed.error_code, "NETWORK_CRASH_OR_TIMEOUT")
        self.assertIn("Connection timed out", claimed.error_detail)

        # Quota remains held pending reconciliation (not prematurely released)
        self.sender.refresh_from_db()
        self.assertEqual(self.sender.daily_action_count, 1)
