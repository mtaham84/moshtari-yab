import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import TestCase, override_settings

from apps.discovery.models import AIAnalysis, Customer, Opportunity
from apps.products.models import Product

from .funnel import funnel
from .tests import EngineSchemaMixin, seller


class FunnelTests(EngineSchemaMixin, TestCase):
    def test_funnel_counts_opportunities_feedback_and_known_x_stages(self):
        user, business = seller("funnel@example.com", "فروشگاه قیف")
        product = Product.objects.create(business=business, name="چراغ", description="")
        since = datetime(2026, 10, 1, tzinfo=timezone.utc)
        until = since + timedelta(days=2)
        customer = Customer.objects.create(business=business, external_user_id="customer")
        for index in range(20):
            opportunity = Opportunity.objects.create(
                business=business, customer=customer, source_platform="x",
                source_raw_message="دنبال کالا هستم",
                feedback="relevant" if index < 15 else "irrelevant",
                status="CONTACTED" if index < 4 else ("CONVERTED" if index == 4 else "NEW"),
            )
            Opportunity.objects.filter(pk=opportunity.pk).update(
                created_at=since + timedelta(hours=index), feedback_at=since + timedelta(hours=index)
            )
        store = self.store()
        store._exec("INSERT INTO {s}.x_filter_decisions (chat_id, message_id, author_id, keep, reason, signals, mode) VALUES (%s,%s,'author',TRUE,'ok','{}','on')",
                    (f"x:p:{product.pk}", 1))
        store.record_x_hit_results([
            (f"x:p:{product.pk}", index, "customer", "matched", f"need_{index}")
            if index < 10 else
            (f"x:p:{product.pk}", index, "not_fit", "wrong product", None)
            for index in range(20)
        ])
        for opportunity in Opportunity.objects.all():
            AIAnalysis.objects.create(opportunity=opportunity, cost_toman=100)
        with override_settings(NEED_ENGINE_SCHEMA=self._ne_store.schema), patch.dict(os.environ, {"NE_X_ENABLED": "true", "NE_X_MODE": "product"}):
            result = funnel("x", since, until, business)
        assert result["shown"] == 20
        assert result["contacted"] == 5
        assert result["sold"] == 1
        assert result["precision_from_feedback"] == 0.75
        assert result["collected"] is None
        assert result["prefiltered_in"] == 1
        assert result["matched"] == 10
        assert result["extracted"] == 10
        assert result["cost_total"] is None
        assert result["cost_per_shown_opportunity"] == 100

    def test_funnel_precision_waits_for_twenty_feedback_labels(self):
        since = datetime(2026, 10, 1, tzinfo=timezone.utc)
        result = funnel("instagram", since, since + timedelta(days=1))
        assert result["precision_from_feedback"] is None
        assert result["extracted"] is None


