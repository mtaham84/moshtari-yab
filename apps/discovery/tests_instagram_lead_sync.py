"""Regression tests for sync_instagram_lead_to_opportunity (tenant resolution, billing, scoring)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.billing.models import BillingSettings, WalletTransaction
from apps.billing.services import apply_transaction
from apps.businesses.models import Business
from apps.discovery.models import InstagramEngineLead, Opportunity
from apps.discovery.services import business_for_tenant, sync_instagram_lead_to_opportunity


def _business(username):
    user = get_user_model().objects.create_user(username=username, password="x")
    return Business.objects.create(user=user, name=f"biz {username}")


def _lead(tenant_id, **extra):
    data = {
        "tenant_id": tenant_id, "ig_user_pk": "9001", "username": "buyer",
        "comment_text": "قیمت چنده؟", "source_post_url": "https://www.instagram.com/p/ABC123/",
    }
    data.update(extra)
    return InstagramEngineLead.objects.create(**data)


class TenantResolutionTests(TestCase):
    def setUp(self):
        self.default_biz = _business("sang@peyda.ir")
        self.other = _business("other@example.com")

    def test_unknown_tenant_is_not_synced_to_any_business(self):
        apply_transaction(self.default_biz, Decimal("1000000"), WalletTransaction.TOPUP)
        lead = _lead("tenant_default")
        self.assertIsNone(sync_instagram_lead_to_opportunity(lead, ai_meta={"cost_usd": 0.01}))
        self.assertEqual(Opportunity.objects.count(), 0)
        self.assertFalse(WalletTransaction.objects.filter(kind=WalletTransaction.USAGE).exists())

    def test_missing_business_is_not_synced(self):
        lead = _lead("tenant_999999")
        self.assertIsNone(sync_instagram_lead_to_opportunity(lead))
        self.assertEqual(Opportunity.objects.count(), 0)

    def test_tenant_id_maps_to_its_business(self):
        lead = _lead(f"tenant_{self.other.pk}")
        opp = sync_instagram_lead_to_opportunity(lead)
        self.assertIsNotNone(opp)
        self.assertEqual(opp.business, self.other)

    def test_legacy_alias_maps_to_its_business(self):
        from unittest.mock import patch

        from apps.discovery.tenants import canonical_tenant

        self.assertEqual(canonical_tenant("sarv"), "tenant_4")
        with patch.dict("apps.discovery.tenants.TENANT_ALIASES", {"legacy": f"tenant_{self.other.pk}"}):
            self.assertEqual(business_for_tenant("legacy"), self.other)
        self.assertIsNone(business_for_tenant(""))
        self.assertIsNone(business_for_tenant("tenant_abc"))


class LeadBillingTests(TestCase):
    def setUp(self):
        self.biz = _business("billing@example.com")
        apply_transaction(self.biz, Decimal("10000000"), WalletTransaction.TOPUP)

    def _usage(self):
        return WalletTransaction.objects.get(wallet__business=self.biz, kind=WalletTransaction.USAGE)

    def test_zero_markup_is_respected(self):
        cfg = BillingSettings.get()
        cfg.markup_percent = Decimal("0")
        cfg.save()
        sync_instagram_lead_to_opportunity(_lead(f"tenant_{self.biz.pk}"), ai_meta={"cost_usd": 1})
        self.assertEqual(self._usage().amount_toman, -cfg.usd_to_toman)

    def test_default_markup_matches_billing_service(self):
        from apps.billing.services import billed_toman

        cfg = BillingSettings.get()
        sync_instagram_lead_to_opportunity(_lead(f"tenant_{self.biz.pk}"), ai_meta={"cost_usd": 1})
        self.assertEqual(self._usage().amount_toman, -billed_toman(1, cfg).quantize(Decimal("1")))

    def test_wallet_failure_is_logged(self):
        from unittest.mock import patch

        with patch("apps.billing.services.apply_transaction", side_effect=RuntimeError("boom")), \
                self.assertLogs("apps.discovery.services", level="ERROR"):
            sync_instagram_lead_to_opportunity(_lead(f"tenant_{self.biz.pk}"), ai_meta={"cost_usd": 1})


class LeadBillingIdempotencyTests(TestCase):
    """BIL-02: re-syncing a lead with the same AI evaluation charges once."""

    def setUp(self):
        self.biz = _business("idem@example.com")
        apply_transaction(self.biz, Decimal("10000000"), WalletTransaction.TOPUP)

    def _usages(self):
        return WalletTransaction.objects.filter(wallet__business=self.biz, kind=WalletTransaction.USAGE)

    def test_same_ai_meta_twice_is_one_debit(self):
        lead = _lead(f"tenant_{self.biz.pk}")
        meta = {"cost_usd": 0.5, "tokens_in": 100, "tokens_out": 20, "model": "gpt-x"}
        sync_instagram_lead_to_opportunity(lead, ai_meta=dict(meta))
        sync_instagram_lead_to_opportunity(lead, ai_meta=dict(meta))
        self.assertEqual(self._usages().count(), 1)
        self.assertIn("gpt-x", self._usages().get().description)

    def test_a_new_evaluation_is_charged(self):
        lead = _lead(f"tenant_{self.biz.pk}")
        sync_instagram_lead_to_opportunity(lead, ai_meta={"cost_usd": 0.5, "tokens_in": 100, "tokens_out": 20})
        sync_instagram_lead_to_opportunity(lead, ai_meta={"cost_usd": 0.6, "tokens_in": 120, "tokens_out": 20})
        self.assertEqual(self._usages().count(), 2)


class BilledTomanTests(TestCase):
    def test_formula(self):
        from apps.billing.services import billed_toman

        cfg = BillingSettings.get()
        cfg.usd_to_toman = Decimal("100000")
        cfg.markup_percent = Decimal("0")
        self.assertEqual(billed_toman(1, cfg), Decimal("100000.00"))
        cfg.markup_percent = Decimal("150")
        self.assertEqual(billed_toman(1, cfg), Decimal("250000.00"))
        self.assertEqual(billed_toman(None, cfg), Decimal("0.00"))


class LeadScoringTests(TestCase):
    def setUp(self):
        from apps.products.models import Product

        self.biz = _business("scoring@example.com")
        self.product = Product.objects.create(business=self.biz, name="دستکش چرمی", description="x", price=100,
                                              status="ACTIVE", is_discovery_active=True)

    def test_zero_intent_stays_zero(self):
        opp = sync_instagram_lead_to_opportunity(_lead(f"tenant_{self.biz.pk}", intent_score=0.0),
                                                 ai_meta={"intent_score": 0.9})
        self.assertEqual(opp.ai_analysis.intent_score, 0.0)

    def test_unrelated_comment_gets_no_product_match(self):
        from apps.discovery.models import OpportunityProductMatch

        opp = sync_instagram_lead_to_opportunity(_lead(f"tenant_{self.biz.pk}", comment_text="سلام وقت بخیر"))
        self.assertEqual(OpportunityProductMatch.objects.filter(opportunity=opp).count(), 0)
        self.assertEqual(opp.ai_analysis.product_fit_score, 0.0)

    def test_related_comment_matches_product(self):
        from apps.discovery.models import OpportunityProductMatch

        opp = sync_instagram_lead_to_opportunity(_lead(f"tenant_{self.biz.pk}", comment_text="قیمت دستکش چنده"))
        self.assertEqual(OpportunityProductMatch.objects.get(opportunity=opp).product, self.product)
        self.assertEqual(opp.ai_analysis.product_fit_score, 0.94)
