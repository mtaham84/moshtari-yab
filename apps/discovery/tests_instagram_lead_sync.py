"""Regression tests for sync_instagram_lead_to_opportunity (tenant resolution, billing, scoring)."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.billing.models import BillingSettings, WalletTransaction
from apps.billing.services import apply_transaction
from apps.businesses.models import Business
from apps.discovery.models import InstagramEngineLead, Opportunity
from apps.discovery.services import business_for_tenant, sync_instagram_lead_to_opportunity


def _business(username, pk=None, **extra):
    user = get_user_model().objects.create_user(username=username, password="x")
    kwargs = {"user": user, "name": f"biz {username}"}
    if pk is not None:
        kwargs["pk"] = pk
    kwargs.update(extra)
    return Business.objects.create(**kwargs)


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

    def test_legacy_alias_maps_to_business_4(self):
        biz4 = _business("legacy@example.com", pk=4)
        self.assertEqual(business_for_tenant("sarv"), biz4)
        self.assertIsNone(business_for_tenant(""))
        self.assertIsNone(business_for_tenant("tenant_abc"))
