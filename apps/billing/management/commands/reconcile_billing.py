from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.billing.reconciliation import FinancialReconciliationService


class Command(BaseCommand):
    help = "Audits financial ledger discrepancies, stale reservations, and balance imbalances (P06.06 / R31, R32, R34)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix-stale-reservations",
            action="store_true",
            help="Safely expire stale reservations that passed their expires_at.",
        )
        parser.add_argument(
            "--pending-grace-minutes",
            type=int,
            default=5,
            help="Minutes to allow pending allocations before flagging as unmapped.",
        )

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting Financial Reconciliation Audit..."))

        fix_stale = options["fix_stale_reservations"]
        grace = options["pending_grace_minutes"]

        if fix_stale:
            expired_count = FinancialReconciliationService.expire_stale_reservations()
            if expired_count:
                self.stdout.write(self.style.SUCCESS(f"Expired {expired_count} stale reservations."))
            else:
                self.stdout.write("No stale reservations found to expire.")

        report = FinancialReconciliationService.audit_discrepancies(pending_grace_minutes=grace)

        self.stdout.write("=" * 60)
        self.stdout.write(f"Reconciliation Report at: {report.generated_at.isoformat()}")
        self.stdout.write(f"Total Anomalies: {report.total_anomalies}")
        self.stdout.write(f"  - Stale Reservations: {report.stale_reservations_count}")
        self.stdout.write(f"  - Unresolved Holds:   {report.unresolved_holds_count}")
        self.stdout.write(f"  - Unmapped Pending:   {report.unmapped_pending_count}")
        self.stdout.write(f"  - Ledger Imbalances:  {report.ledger_imbalances_count}")
        self.stdout.write("=" * 60)

        for item in report.items:
            style_fn = self.style.ERROR if item.severity == "ERROR" else self.style.WARNING
            self.stdout.write(style_fn(f"[{item.category}] {item.identifier}: {item.description} ({item.amount_toman:,.2f} Toman)"))

        if report.is_clean:
            self.stdout.write(self.style.SUCCESS("All financial ledgers and balances are 100% reconciled and clean."))
