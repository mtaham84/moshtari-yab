from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model

from apps.businesses.models import Business
from apps.billing.models import (
    BillingSettings,
    CreditWaitingQueue,
    Wallet,
    WalletReservation,
    WalletTransaction,
)
from apps.billing.credit_queue_service import CreditQueueService
from apps.billing.services import apply_transaction

User = get_user_model()


class CreditQueueTestCase(TestCase):
    """
    P06.04 Verification: Authoritative credit waiting queue, idempotent enqueueing,
    safe FIFO resumption on wallet top-up, and dead job protection (R32, R07).
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="queue_owner", email="qowner@test.local")
        self.biz = Business.objects.create(name="Queue Biz", user=self.user)
        self.wallet, _ = Wallet.objects.get_or_create(business=self.biz)
        self.wallet.balance_toman = Decimal("0.00")
        self.wallet.save(update_fields=["balance_toman"])

        self.settings = BillingSettings.get()
        self.settings.enforce_balance = True
        self.settings.min_balance_toman = Decimal("0.00")
        self.settings.save()

    def test_zero_credit_enqueues_single_job_zero_calls_zero_debit(self):
        """Zero credit enqueues exactly 1 work item, 0 paid calls, 0 debits, status WAITING."""
        item = CreditQueueService.enqueue_waiting_work(
            business=self.biz,
            operation_id="op-q-1",
            stage="need_extraction",
            payload={"chat_id": "test_chat_1", "messages": ["msg1"]},
            estimated_toman=Decimal("500.00"),
        )

        self.assertIsNotNone(item.work_item_id)
        self.assertEqual(item.status, CreditWaitingQueue.STATUS_WAITING)
        self.assertEqual(item.estimated_toman, Decimal("500.00"))

        # Zero debit to wallet
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("0.00"))
        self.assertFalse(WalletTransaction.objects.filter(wallet=self.wallet).exists())
        self.assertFalse(WalletReservation.objects.filter(operation_id="op-q-1").exists())

    def test_re_enqueuing_same_operation_updates_record_without_duplicate(self):
        """Calling enqueue_waiting_work with identical operation_id updates the existing row."""
        item1 = CreditQueueService.enqueue_waiting_work(
            business=self.biz,
            operation_id="op-q-idempotent",
            stage="chat_json",
            payload={"version": 1},
            estimated_toman=Decimal("300.00"),
        )

        # Enqueue again with updated payload
        item2 = CreditQueueService.enqueue_waiting_work(
            business=self.biz,
            operation_id="op-q-idempotent",
            stage="chat_json",
            payload={"version": 2},
            estimated_toman=Decimal("400.00"),
        )

        self.assertEqual(item1.work_item_id, item2.work_item_id)
        self.assertEqual(item2.retry_count, 1)
        self.assertEqual(item2.payload, {"version": 2})
        self.assertEqual(item2.estimated_toman, Decimal("400.00"))

        # Exactly 1 row in DB
        self.assertEqual(
            CreditWaitingQueue.objects.filter(business=self.biz, operation_id="op-q-idempotent").count(),
            1,
        )

    def test_topup_resumes_waiting_jobs_with_valid_reservation(self):
        """Top-up triggers resumption of eligible jobs in FIFO order, creating reservations."""
        # Enqueue 2 jobs: 1000 Toman and 2000 Toman
        item1 = CreditQueueService.enqueue_waiting_work(
            business=self.biz,
            operation_id="op-q-fifo-1",
            stage="extract",
            payload={"task": 1},
            estimated_toman=Decimal("1000.00"),
        )
        item2 = CreditQueueService.enqueue_waiting_work(
            business=self.biz,
            operation_id="op-q-fifo-2",
            stage="extract",
            payload={"task": 2},
            estimated_toman=Decimal("2000.00"),
        )

        # Top up 1500 Toman: only enough for item1 (1000), NOT item2 (needs 2000)
        self.wallet.balance_toman = Decimal("1500.00")
        self.wallet.save(update_fields=["balance_toman"])

        resumed = CreditQueueService.check_and_resume_eligible(self.biz)

        self.assertEqual(len(resumed), 1)
        self.assertEqual(resumed[0].work_item_id, item1.work_item_id)
        self.assertEqual(resumed[0].status, CreditWaitingQueue.STATUS_RESUMED)

        # Item 1 now has an active reservation
        res1 = WalletReservation.objects.filter(operation_id="op-q-fifo-1").first()
        self.assertIsNotNone(res1)
        self.assertEqual(res1.status, WalletReservation.STATUS_HELD)

        # Item 2 remains WAITING
        item2.refresh_from_db()
        self.assertEqual(item2.status, CreditWaitingQueue.STATUS_WAITING)

        # Now top up another 5000 Toman: Item 2 resumes!
        self.wallet.balance_toman = Decimal("6500.00")
        self.wallet.save(update_fields=["balance_toman"])

        resumed2 = CreditQueueService.check_and_resume_eligible(self.biz)
        self.assertEqual(len(resumed2), 1)
        self.assertEqual(resumed2[0].work_item_id, item2.work_item_id)
        self.assertEqual(resumed2[0].status, CreditWaitingQueue.STATUS_RESUMED)

    def test_cancelled_job_is_never_resumed_on_topup(self):
        """Cancelled work item is never auto-resumed even with abundant credit."""
        item = CreditQueueService.enqueue_waiting_work(
            business=self.biz,
            operation_id="op-cancel-test",
            stage="verify",
            payload={"data": "cancel"},
            estimated_toman=Decimal("500.00"),
        )

        # Cancel item
        cancelled = CreditQueueService.cancel_waiting_work(item.work_item_id)
        self.assertTrue(cancelled)

        item.refresh_from_db()
        self.assertEqual(item.status, CreditWaitingQueue.STATUS_CANCELLED)

        # Top up large balance
        self.wallet.balance_toman = Decimal("50000.00")
        self.wallet.save(update_fields=["balance_toman"])

        resumed = CreditQueueService.check_and_resume_eligible(self.biz)
        self.assertEqual(len(resumed), 0)

        item.refresh_from_db()
        self.assertEqual(item.status, CreditWaitingQueue.STATUS_CANCELLED)
        self.assertFalse(WalletReservation.objects.filter(operation_id="op-cancel-test").exists())

    def test_waiting_summary(self):
        """Summary returns accurate count and stage distribution."""
        CreditQueueService.enqueue_waiting_work(self.biz, "op-s1", "extract", {}, Decimal("100.00"))
        CreditQueueService.enqueue_waiting_work(self.biz, "op-s2", "extract", {}, Decimal("200.00"))
        CreditQueueService.enqueue_waiting_work(self.biz, "op-s3", "reply", {}, Decimal("300.00"))

        summary = CreditQueueService.get_waiting_summary(self.biz)
        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["total_estimated_toman"], Decimal("600.00"))
        self.assertEqual(summary["by_stage"], {"extract": 2, "reply": 1})
