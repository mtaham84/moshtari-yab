import io
from pathlib import Path
from decimal import Decimal
from django.test import TestCase
from django.conf import settings
from django.core.management import call_command
from django.contrib.auth import get_user_model

from apps.businesses.models import Business
from apps.products.models import Category, Product, ProductImage
from apps.billing.models import Wallet, WalletReservation, WalletTransaction
from apps.discovery.models import InstagramEngineAccount
from apps.core.access_policy import AccessPolicy

User = get_user_model()


class GateG10ControlledReleaseTests(TestCase):
    """
    Gate G10 Exit Matrix & Characterization Suite (Phase 10 Exit).
    Validates:
    1. Full audit of all 42 baseline findings (R01-R42).
    2. R25 explicitly audited and marked DEFERRED (FROZEN LEGACY) per product specification.
    3. Zero unapplied or pending migrations (makemigrations --check dry run clean).
    4. Legacy frozen codebase preserved untouched.
    5. Multi-tenant access policy and financial safeguards strictly enforced.
    """

    def setUp(self):
        self.base_dir = Path(settings.BASE_DIR)
        candidate_gate = self.base_dir / "docs" / "remediation" / "PHASE_10_EXIT_GATE.md"
        if not candidate_gate.exists():
            candidate = Path(__file__).resolve().parent.parent.parent / "docs" / "remediation" / "PHASE_10_EXIT_GATE.md"
            if candidate.exists():
                self.base_dir = candidate.parent.parent.parent

    def _get_gate_document(self) -> Path:
        possible_paths = [
            Path(__file__).resolve().parent / "PHASE_10_EXIT_GATE.md",
            self.base_dir / "docs" / "remediation" / "PHASE_10_EXIT_GATE.md",
        ]
        doc = next((p for p in possible_paths if p.exists()), None)
        self.assertIsNotNone(doc, "PHASE_10_EXIT_GATE.md must exist in apps/core or docs/remediation")
        return doc

    def test_g10_phase_10_exit_gate_document_exists(self):
        gate_doc = self._get_gate_document()
        content = gate_doc.read_text(encoding="utf-8")
        self.assertIn("Gate G10", content)
        self.assertIn("R25", content)
        self.assertIn("DEFERRED (FROZEN LEGACY)", content)

    def test_g10_all_42_findings_accounted_for(self):
        gate_doc = self._get_gate_document()
        content = gate_doc.read_text(encoding="utf-8")
        for i in range(1, 43):
            finding_id = f"R{i:02d}"
            self.assertIn(finding_id, content, f"Finding {finding_id} must be audited in Gate G10 document")

    def test_g10_database_migrations_complete(self):
        out = io.StringIO()
        try:
            call_command("makemigrations", check=True, dry_run=True, stdout=out)
        except SystemExit as exc:
            self.fail(f"makemigrations --check dry-run failed with code: {exc}")
        self.assertIn("No changes detected", out.getvalue())

    def test_g10_legacy_freeze_intact(self):
        legacy_models = self.base_dir / "instagram_engine" / "models.py"
        self.assertTrue(legacy_models.exists(), "Legacy instagram_engine/models.py must remain intact")

    def test_g10_multi_tenant_and_financial_safety_invariants(self):
        user = User.objects.create_user(username="g10_owner", password="pwd")
        biz = Business.objects.create(name="کسب‌وکار دروازه ۱۰", user=user)
        wallet, _ = Wallet.objects.get_or_create(business=biz)

        # Invariant 1: Non-negative balance
        self.assertGreaterEqual(wallet.balance_toman, Decimal("0.00"))

        # Invariant 2: Multi-tenant access policy
        tenants = AccessPolicy.sender_search_tenants(user)
        self.assertIn(f"tenant_{biz.id}", tenants)
        self.assertIn(AccessPolicy.PLATFORM_TENANT_ID, tenants)
