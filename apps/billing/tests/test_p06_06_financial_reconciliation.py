from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.businesses.models import Business
from apps.billing.models import (
    BillingSettings,
    CostAllocation,
    CostEvent,
    Wallet,
    WalletReservation,
    WalletTransaction,
)
from apps.billing.reconciliation import FinancialReconciliationService

User = get_user_model()


class FinancialReconciliationTestCase(TestCase):
    """
    P06.06 Verification: Discrepancy detection (stale reservations, ledger imbalance,
    unresolved holds), cutover safety, and management command (R31, R32, R34).
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create(username="recon_user", email="recon@test.local")
        self.biz = Business.objects.create(name="Recon Biz", user=self.user)
        self.wallet, _ = Wallet.objects.get_or_create(business=self.biz)
        self.wallet.balance_toman = Decimal("10000.00")
        self.wallet.save(update_fields=["balance_toman"])

        # Create matching transaction so ledger is initially balanced
        WalletTransaction.objects.create(
            wallet=self.wallet,
            kind=WalletTransaction.TOPUP,
            amount_toman=Decimal("10000.00"),
            balance_after=Decimal("10000.00"),
            description="Initial top-up",
        )

    def test_clean_state_reports_zero_anomalies(self):
        """When data is completely synchronized, reconciliation report is clean."""
        report = FinancialReconciliationService.audit_discrepancies()
        self.assertTrue(report.is_clean)
        self.assertEqual(report.total_anomalies, 0)

    def test_synthetic_discrepancies_detected(self):
        """Synthetic stale reservation, unresolved hold, and balance imbalance are all identified."""
        now = timezone.now()

        # 1. Inject stale reservation (expired 1 hour ago)
        res_stale = WalletReservation.objects.create(
            reservation_id="res-stale-01",
            operation_id="op-stale-01",
            wallet=self.wallet,
            reserved_amount_toman=Decimal("3000.00"),
            status=WalletReservation.STATUS_HELD,
            expires_at=now - timedelta(hours=1),
        )

        # 2. Inject unresolved cost allocation
        evt = CostEvent.objects.create(
            operation_id="op-unres-01",
            attempt_id="att-unres-01",
            provider_name="gemini",
            model_name="gemini-flash",
            stage="test",
            usd_to_toman_rate=Decimal("268000"),
            markup_multiplier=Decimal("1.50"),
            is_unknown=True,
        )
        alloc_unres = CostAllocation.objects.create(
            cost_event=evt,
            business=self.biz,
            part_index=0,
            allocated_toman=Decimal("1500.00"),
            status=CostAllocation.STATUS_UNRESOLVED,
        )

        # 3. Inject ledger imbalance: modify wallet balance directly without transaction
        self.wallet.balance_toman = Decimal("12500.00")  # Should be 10000 according to transactions!
        self.wallet.save(update_fields=["balance_toman"])

        report = FinancialReconciliationService.audit_discrepancies()

        self.assertFalse(report.is_clean)
        self.assertEqual(report.total_anomalies, 3)
        self.assertEqual(report.stale_reservations_count, 1)
        self.assertEqual(report.unresolved_holds_count, 1)
        self.assertEqual(report.ledger_imbalances_count, 1)

        categories = [item.category for item in report.items]
        self.assertIn("STALE_RESERVATION", categories)
        self.assertIn("UNRESOLVED_HOLD", categories)
        self.assertIn("LEDGER_IMBALANCE", categories)

    def test_expire_stale_reservations_releases_phantom_holds(self):
        """expire_stale_reservations marks past reservations EXPIRED without creating charges."""
        now = timezone.now()
        res_stale = WalletReservation.objects.create(
            reservation_id="res-stale-02",
            operation_id="op-stale-02",
            wallet=self.wallet,
            reserved_amount_toman=Decimal("2500.00"),
            status=WalletReservation.STATUS_HELD,
            expires_at=now - timedelta(minutes=30),
        )

        expired_count = FinancialReconciliationService.expire_stale_reservations()
        self.assertEqual(expired_count, 1)

        res_stale.refresh_from_db()
        self.assertEqual(res_stale.status, WalletReservation.STATUS_EXPIRED)

        # Confirm wallet balance was NOT debited
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance_toman, Decimal("10000.00"))

    def test_cutover_boundary_does_not_debit_historical_unmapped(self):
        """Historical items prior to cutover are never marked for automated settlement."""
        cutover_time = timezone.now()

        # Historical pending allocation from yesterday
        evt_hist = CostEvent.objects.create(
            operation_id="op-hist",
            attempt_id="att-hist",
            provider_name="openai",
            model_name="gpt-4o",
            stage="test",
            usd_to_toman_rate=Decimal("268000"),
        )
        alloc_hist = CostAllocation.objects.create(
            cost_event=evt_hist,
            business=self.biz,
            allocated_toman=Decimal("4000.00"),
            status=CostAllocation.STATUS_PENDING,
        )
        # Artificially set created_at in the past
        CostAllocation.objects.filter(pk=alloc_hist.pk).update(created_at=cutover_time - timedelta(days=2))

        # Audit with cutover boundary: historical pending allocation is NOT flagged as an active pending anomaly
        report = FinancialReconciliationService.audit_discrepancies(cutover_timestamp=cutover_time)
        self.assertEqual(report.unmapped_pending_count, 0)

    def test_reconcile_billing_management_command(self):
        """Management command reconcile_billing executes cleanly and prints report."""
        out = StringIO()
        call_command("reconcile_billing", "--fix-stale-reservations", stdout=out)
        output = out.getvalue()

        self.assertIn("Starting Financial Reconciliation Audit", output)
        self.assertIn("Reconciliation Report at:", output)
        self.assertIn("Total Anomalies:", output)
