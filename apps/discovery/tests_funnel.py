import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from apps.discovery.models import AIAnalysis, Customer, Opportunity
from apps.products.models import Product

from .funnel import _extracted_count, _matched_count, _prefiltered_in_count, funnel
from .tests import EngineSchemaMixin, seller


class UnsupportedFunnelStageTests(SimpleTestCase):
    def test_unsupported_sources_return_before_checking_engine_tables(self):
        since = datetime(2026, 10, 1, tzinfo=timezone.utc)
        until = since + timedelta(days=1)
        for count in (_prefiltered_in_count, _extracted_count, _matched_count):
            for source in ("telegram", "instagram"):
                with self.subTest(count=count.__name__, source=source):
                    with patch("apps.discovery.funnel._table_exists", side_effect=AssertionError("database accessed")):
                        self.assertIsNone(count(source, since, until))

    def test_x_engine_stages_use_stage_specific_sql_and_bound_parameters(self):
        since = datetime(2026, 10, 1, tzinfo=timezone.utc)
        until = since + timedelta(days=1)

        for count, expected in (
            (_prefiltered_in_count, "e.keep AND e.mode = 'on'"),
            (_extracted_count, "e.need_id IS NOT NULL"),
            (_matched_count, "e.outcome = 'customer'"),
        ):
            with self.subTest(stage=count.__name__), patch("apps.discovery.funnel._table_exists", return_value=True), patch(
                "apps.discovery.funnel._query_count", return_value=7
            ) as query_count:
                self.assertEqual(count("x", since, until), 7)
            sql, params = query_count.call_args.args
            self.assertIn(expected, sql)
            self.assertIn("LIKE %s", sql)
            self.assertNotIn("%'", sql)
            self.assertEqual(params, [since, until, "x:%"])


class FunnelTests(EngineSchemaMixin, TestCase):
    def test_x_funnel_stages_bind_chat_prefix_across_modes_and_businesses(self):
        _, business = seller("funnel-matrix@example.com", "فروشگاه آزمایش")
        since = datetime(2026, 10, 1, tzinfo=timezone.utc)
        until = since + timedelta(days=1)

        for mode in ("product", "query"):
            for scoped_business in (None, business):
                with self.subTest(mode=mode, business=scoped_business), patch.dict(
                    os.environ, {"NE_X_PREFILTER": "on", "NE_X_ENABLED": "true", "NE_X_MODE": mode}
                ), patch("apps.discovery.funnel._schema_table_exists", return_value=False), patch(
                    "apps.discovery.funnel._table_exists", return_value=True
                ), patch("apps.discovery.funnel._query_count", return_value=0) as query_count:
                    funnel("x", since, until, scoped_business)

                expected_stages = 3 if mode == "product" else 1
                self.assertEqual(query_count.call_count, expected_stages)
                for sql, params in (call.args for call in query_count.call_args_list):
                    self.assertIn("e.chat_id LIKE %s", sql)
                    self.assertIn("x:%", params)
                    self.assertNotIn("LIKE 'x:%'", sql)

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
        decided_at = since + timedelta(hours=1)
        store._exec("INSERT INTO {s}.x_filter_decisions (chat_id, message_id, author_id, keep, reason, signals, mode, decided_at) VALUES (%s,%s,'author',TRUE,'ok','{}','on',%s)",
                    (f"x:p:{product.pk}", 1, decided_at))
        store.record_x_hit_results([
            (f"x:p:{product.pk}", index, "customer", "matched", f"need_{index}")
            if index < 10 else
            (f"x:p:{product.pk}", index, "not_fit", "wrong product", None)
            for index in range(20)
        ])
        store._exec("UPDATE {s}.x_hit_results SET decided_at = %s WHERE chat_id = %s",
                    (decided_at, f"x:p:{product.pk}"))
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


