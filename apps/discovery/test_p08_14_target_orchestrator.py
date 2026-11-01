"""
Tests for target scraper orchestrator seam (P08.14 / R39).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.businesses.models import Business
from apps.billing.services import ensure_wallet
from apps.discovery.family_inspection_service import (
    FamilyInspectionResult,
    FamilyInspectionService,
)
from apps.discovery.models import (
    InstagramCrawlExecution,
    InstagramEngineAccount,
    InstagramSourceStat,
)
from apps.discovery.target_orchestrator import TargetScrapeOrchestrator

User = get_user_model()


class TargetScrapeOrchestratorTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(username="orch_user_test")
        self.business = Business.objects.create(
            user=self.user,
            name="کسب و کار تست",
            business_domain="مد و پوشاک",
        )
        wallet = ensure_wallet(self.business)
        wallet.balance_toman = 500_000
        wallet.save(update_fields=["balance_toman"])
        self.account = InstagramEngineAccount.objects.create(
            username="bot_orchestrator",
            status="ACTIVE",
        )
        self.family = SimpleNamespace(
            product_id=101,
            category_path="پوشاک",
            titles=["کت تک"],
            notes="",
        )
        self.plan = SimpleNamespace(
            buyer_signals=["قیمت"],
            negative_signals=[],
        )
        self.source = SimpleNamespace(
            family_id=101,
            source_type="hashtag",
            label="کت_تک",
        )
        self.target = {
            "business": self.business,
            "family": self.family,
            "plan": self.plan,
            "tenant_id": str(self.business.pk),
            "sources": [self.source],
        }
        self.mock_inspector = MagicMock(spec=FamilyInspectionService)

    def test_skips_when_business_is_blocked(self):
        orchestrator = TargetScrapeOrchestrator(
            family_inspector=self.mock_inspector,
        )
        with self.settings(DEFAULT_BUSINESS_CREDIT=-100):
            # Patch is_blocked to return True
            with patch("apps.discovery.target_orchestrator.is_blocked", return_value=True):
                leads = orchestrator.scrape_family_target(
                    cl=None,
                    account=self.account,
                    target=self.target,
                    min_comments=10,
                    max_posts=20,
                )
                self.assertEqual(leads, 0)
                self.mock_inspector.inspect_family_medias.assert_not_called()

    def test_successful_scrape_records_execution(self):
        self.mock_inspector.inspect_family_medias.return_value = FamilyInspectionResult(
            posts_inspected=15,
            qualifying_posts=3,
            leads_saved=2,
            retired=False,
        )
        orchestrator = TargetScrapeOrchestrator(
            family_inspector=self.mock_inspector,
            fetch_medias_fn=lambda **kwargs: [SimpleNamespace(id="m1")],
        )
        leads = orchestrator.scrape_family_target(
            cl=None,
            account=self.account,
            target=self.target,
            min_comments=10,
            max_posts=20,
        )
        self.assertEqual(leads, 2)
        exec_record = InstagramCrawlExecution.objects.filter(
            business=self.business,
            query_text="#کت_تک",
        ).first()
        self.assertIsNotNone(exec_record)
        self.assertEqual(exec_record.status, "SUCCESS")
        self.assertEqual(exec_record.leads_found, 2)

    def test_retires_unqualified_source(self):
        self.mock_inspector.inspect_family_medias.return_value = FamilyInspectionResult(
            posts_inspected=20,
            qualifying_posts=1,  # Less than target 3
            leads_saved=0,
            retired=True,
        )
        retire_called = []

        def mock_retire(**kwargs):
            retire_called.append(kwargs)

        orchestrator = TargetScrapeOrchestrator(
            family_inspector=self.mock_inspector,
            fetch_medias_fn=lambda **kwargs: [SimpleNamespace(id="m2")],
            retire_source_fn=mock_retire,
        )
        leads = orchestrator.scrape_family_target(
            cl=None,
            account=self.account,
            target=self.target,
            min_comments=10,
            max_posts=20,
        )
        self.assertEqual(leads, 0)
        self.assertEqual(len(retire_called), 1)
        exec_record = InstagramCrawlExecution.objects.filter(
            business=self.business,
            status="REMOVED_UNQUALIFIED",
        ).first()
        self.assertIsNotNone(exec_record)
        self.assertEqual(exec_record.posts_inspected, 20)
