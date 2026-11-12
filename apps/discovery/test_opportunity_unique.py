"""Tests for the partial unique constraint on Opportunity(business, engine_opportunity_id) (Roadmap 7.4 / B-6)."""
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, models
from django.db.models import Q
from django.test import SimpleTestCase, TestCase

from apps.businesses.models import Business
from apps.discovery.models import Customer, Opportunity

User = get_user_model()


class OpportunityUniqueConstraintUnitTests(SimpleTestCase):
    """Fast unit tests (no DB required) for constraint definition and duplicate-merge migration logic."""

    def test_opportunity_meta_defines_partial_unique_constraint(self):
        constraints = {c.name: c for c in Opportunity._meta.constraints if isinstance(c, models.UniqueConstraint)}
        self.assertIn("uniq_opportunity_business_engine_id", constraints)
        c = constraints["uniq_opportunity_business_engine_id"]
        self.assertEqual(tuple(c.fields), ("business", "engine_opportunity_id"))
        self.assertEqual(c.condition, ~Q(engine_opportunity_id=""))

    def test_migration_merges_duplicate_engine_opportunities_before_constraint(self):
        mig = import_module("apps.discovery.migrations.0043_opportunity_unique_engine_id")

        keep = SimpleNamespace(
            pk=10, id=10, business_id=1, engine_opportunity_id="need_5",
            status="NEW", feedback="", feedback_at=None, feedback_reason="",
            lead_feedback=None, lead_feedback_at=None, trace_metadata={"engine": {}},
            save=MagicMock(),
        )
        dup = SimpleNamespace(
            pk=11, id=11, business_id=1, engine_opportunity_id="need_5",
            status="CONTACTED", feedback="good", feedback_at="2026-11-10", feedback_reason="",
            lead_feedback="good", lead_feedback_at="2026-11-10",
            trace_metadata={"seller_drafts": {"1": "سلام"}},
            save=MagicMock(),
        )

        opp_qs = MagicMock()
        opp_qs.exclude.return_value = opp_qs
        opp_qs.values.return_value = opp_qs
        opp_qs.annotate.return_value = opp_qs
        opp_qs.filter.return_value = opp_qs
        opp_qs.order_by.return_value = [keep, dup]
        opp_qs.__iter__.return_value = iter([{"business_id": 1, "engine_opportunity_id": "need_5", "n": 2}])

        opp_model = MagicMock()
        opp_model.objects = opp_qs

        ai_model = MagicMock()
        ai_model.objects.filter.return_value.exists.return_value = True

        match_model = MagicMock()
        match_model.objects.filter.return_value.values_list.return_value = [1]

        ev_model = MagicMock()
        ev_model.objects.filter.return_value.exists.return_value = True

        order_model = MagicMock()
        reply_model = MagicMock()
        reply_model.objects.filter.return_value.values_list.return_value = []

        models_by_name = {
            "Opportunity": opp_model,
            "AIAnalysis": ai_model,
            "OpportunityProductMatch": match_model,
            "Evidence": ev_model,
            "InstagramReplySuggestion": reply_model,
            "ProductOrder": order_model,
        }
        apps_registry = MagicMock()
        apps_registry.get_model.side_effect = lambda app, name: models_by_name[name]

        mig.merge_duplicate_engine_opportunities(apps_registry, MagicMock())

        self.assertEqual(keep.status, "CONTACTED")
        self.assertEqual(keep.feedback, "good")
        self.assertEqual(keep.trace_metadata["seller_drafts"], {"1": "سلام"})
        keep.save.assert_called_once()
        order_model.objects.filter.assert_called_once_with(opportunity_id__in=[11])
        order_model.objects.filter.return_value.update.assert_called_once_with(opportunity_id=10)
        opp_qs.filter.assert_any_call(parent_opportunity_id__in=[11])
        opp_qs.filter.assert_any_call(pk__in=[11])
        opp_qs.filter.return_value.delete.assert_called()


class OpportunityUniqueConstraintIntegrationTests(TestCase):
    """Full PostgreSQL integration tests for partial unique constraint behavior."""

    def setUp(self):
        u1 = User.objects.create_user(username="u1_uniq@test.ir", password="p")
        u2 = User.objects.create_user(username="u2_uniq@test.ir", password="p")
        self.b1 = Business.objects.create(user=u1, name="فروشگاه ۱", onboarding_done=True)
        self.b2 = Business.objects.create(user=u2, name="فروشگاه ۲", onboarding_done=True)
        self.c1 = Customer.objects.create(business=self.b1, source_platform="telegram", external_user_id="1001")
        self.c2 = Customer.objects.create(business=self.b2, source_platform="telegram", external_user_id="1001")

    def test_duplicate_engine_id_for_same_business_is_rejected(self):
        Opportunity.objects.create(
            business=self.b1, customer=self.c1, source_platform="telegram",
            engine_opportunity_id="need_dup_1", source_raw_message="نیاز ۱",
        )
        with self.assertRaises(IntegrityError):
            Opportunity.objects.create(
                business=self.b1, customer=self.c1, source_platform="telegram",
                engine_opportunity_id="need_dup_1", source_raw_message="نیاز تکراری",
            )

    def test_same_engine_id_allowed_across_different_businesses(self):
        Opportunity.objects.create(
            business=self.b1, customer=self.c1, source_platform="telegram",
            engine_opportunity_id="need_shared_1", source_raw_message="نیاز",
        )
        opp2 = Opportunity.objects.create(
            business=self.b2, customer=self.c2, source_platform="telegram",
            engine_opportunity_id="need_shared_1", source_raw_message="نیاز",
        )
        self.assertIsNotNone(opp2.pk)

    def test_multiple_blank_engine_ids_allowed_for_same_business(self):
        o1 = Opportunity.objects.create(
            business=self.b1, customer=self.c1, source_platform="instagram",
            source_message_id="ig:1", engine_opportunity_id="", source_raw_message="کامنت ۱",
        )
        o2 = Opportunity.objects.create(
            business=self.b1, customer=self.c1, source_platform="instagram",
            source_message_id="ig:2", engine_opportunity_id="", source_raw_message="کامنت ۲",
        )
        self.assertNotEqual(o1.pk, o2.pk)
