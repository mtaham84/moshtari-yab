from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Wallet, WalletTransaction
from apps.businesses.models import Business
from apps.discovery.models import XSearchQuery


class DashboardRealMetricsTests(TestCase):
    """ADM-04: dashboard numbers come from the database, not constants."""

    def setUp(self):
        self.user = User.objects.create_superuser(username="ops", email="ops@test.com", password="x")
        self.business = Business.objects.create(name="کسب‌وکار", user=self.user)
        self.client.force_login(self.user)

    def test_active_x_queries_are_counted(self):
        XSearchQuery.objects.create(business=self.business, text="a", normalized_text="a", state="active")
        XSearchQuery.objects.create(business=self.business, text="b", normalized_text="b", state="active")
        XSearchQuery.objects.create(business=self.business, text="c", normalized_text="c", state="paused")
        res = self.client.get(reverse("manage:dashboard"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.context["x_active_queries_count"], 2)

    def test_financial_rows_show_what_was_actually_debited(self):
        wallet, _ = Wallet.objects.get_or_create(business=self.business)
        WalletTransaction.objects.create(wallet=wallet, kind="USAGE", amount_toman=Decimal("-12345"),
                                         balance_after=Decimal("0"), cost_usd=Decimal("0.01"), calls=3)
        res = self.client.get(reverse("manage:dashboard"))
        self.assertEqual(res.status_code, 200)
        row = next(r for r in res.context["financial_rows"] if r["business"].pk == self.business.pk)
        self.assertEqual(row["spent"], Decimal("12345"))
        self.assertNotIn("total_payable", row)
        self.assertEqual(res.context["total_spent_all"], Decimal("12345"))
        self.assertContains(res, "مبلغ واقعی کسرشده")

    def test_sent_dms_include_replied_leads(self):
        # ADM-05: "DM sent" means DM_DELIVERED_STATUSES everywhere.
        from apps.discovery.models import InstagramEngineLead

        for i, status in enumerate(["DM_SENT", "REPLIED", "NEW"]):
            InstagramEngineLead.objects.create(tenant_id=f"tenant_{self.business.pk}", username=f"u{i}",
                                               ig_user_pk=f"{i}", status=status)
        res = self.client.get(reverse("manage:dashboard"))
        row = next(r for r in res.context["financial_rows"] if r["business"].pk == self.business.pk)
        self.assertEqual(row["dms_sent"], 2)
        matrix = next(r for r in res.context["tenant_matrix"] if r["business"].pk == self.business.pk)
        self.assertEqual(matrix["dms_sent"], 2)

    def test_query_count_does_not_grow_with_businesses(self):
        # ADM-03: the per-business loops used ~10 queries per business.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(reverse("manage:dashboard")).status_code, 200)
            return len(ctx.captured_queries)

        for i in range(2):
            u = User.objects.create_user(username=f"s{i}", password="x")
            Business.objects.create(name=f"b{i}", user=u)
        count_queries()  # warm caches (settings rows, etc.)
        small = count_queries()
        for i in range(2, 10):
            u = User.objects.create_user(username=f"s{i}", password="x")
            Business.objects.create(name=f"b{i}", user=u)
        self.assertEqual(count_queries(), small)
