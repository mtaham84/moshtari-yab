"""Tests for the partial unique constraint on Opportunity(business, engine_opportunity_id) (Roadmap 7.4 / B-6)."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, models
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Q
from django.test import SimpleTestCase, TestCase, TransactionTestCase

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


BEFORE_MERGE = [("discovery", "0042_instagramenginelead_reserved_at")]


def _discovery_leaf(executor: MigrationExecutor) -> list[tuple[str, str]]:
    return [node for node in executor.loader.graph.leaf_nodes() if node[0] == "discovery"]


class MergeDuplicatesMigrationTests(TransactionTestCase):
    """Runs the real migrations on PostgreSQL with duplicates in the table (the case the migration exists for)."""

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE_MERGE)
        apps = executor.loader.project_state(BEFORE_MERGE).apps
        user_model = apps.get_model(settings.AUTH_USER_MODEL)
        business_model = apps.get_model("businesses", "Business")
        customer_model = apps.get_model("discovery", "Customer")
        opportunity_model = apps.get_model("discovery", "Opportunity")
        product_model = apps.get_model("products", "Product")
        b1 = business_model.objects.create(user=user_model.objects.create(username="m1@test.ir"), name="b1")
        b2 = business_model.objects.create(user=user_model.objects.create(username="m2@test.ir"), name="b2")
        c1 = customer_model.objects.create(business=b1, source_platform="telegram", external_user_id="1001")
        c2 = customer_model.objects.create(business=b2, source_platform="telegram", external_user_id="1001")
        p1 = product_model.objects.create(business=b1, name="p1", product_type="PHYSICAL", description="d", price=1)
        p2 = product_model.objects.create(business=b1, name="p2", product_type="PHYSICAL", description="d", price=2)

        def opp(business, customer, engine_id, **kw):
            return opportunity_model.objects.create(business=business, customer=customer, source_platform="telegram",
                                                    engine_opportunity_id=engine_id, source_raw_message="m", **kw)

        self.keep = opp(b1, c1, "need_1")
        dup = opp(b1, c1, "need_1", status="CONTACTED", feedback="good",
                  trace_metadata={"seller_drafts": {str(p1.pk): "draft"}})
        dup2 = opp(b1, c1, "need_1")
        self.other_seller = opp(b2, c2, "need_1")
        self.blank = [opp(b1, c1, ""), opp(b1, c1, "")]
        self.child = opp(b1, c1, "need_child", parent_opportunity=dup)
        analysis = apps.get_model("discovery", "AIAnalysis")
        analysis.objects.create(opportunity=dup, need="n2", intent_score=0.5, product_fit_score=0.5, confidence=0.5)
        analysis.objects.create(opportunity=dup2, need="n3", intent_score=0.5, product_fit_score=0.5, confidence=0.5)
        match = apps.get_model("discovery", "OpportunityProductMatch")
        match.objects.create(opportunity=self.keep, product=p1, rank=1, match_score=0.9)
        match.objects.create(opportunity=dup, product=p1, rank=1, match_score=0.8)
        match.objects.create(opportunity=dup, product=p2, rank=2, match_score=0.7)
        evidence = apps.get_model("discovery", "Evidence")
        evidence.objects.create(opportunity=dup, evidence_type="customer_message", content="e2", source_reference="r")
        evidence.objects.create(opportunity=dup2, evidence_type="customer_message", content="e3", source_reference="r")
        apps.get_model("discovery", "ProductOrder").objects.create(
            product=p1, opportunity=dup2, customer_name="x", customer_phone="09120000000", shipping_address="a")

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_duplicates_are_merged_and_the_constraint_is_added(self):
        executor = MigrationExecutor(connection)
        target = _discovery_leaf(executor)
        executor.migrate(target)
        apps = executor.loader.project_state(target).apps
        opportunity_model = apps.get_model("discovery", "Opportunity")

        rows = opportunity_model.objects.filter(business__name="b1", engine_opportunity_id="need_1")
        self.assertEqual(list(rows.values_list("pk", flat=True)), [self.keep.pk])
        keep = rows.get()
        self.assertEqual((keep.status, keep.feedback), ("CONTACTED", "good"))
        self.assertIn("seller_drafts", keep.trace_metadata)
        self.assertTrue(opportunity_model.objects.filter(pk=self.other_seller.pk).exists())
        self.assertEqual(opportunity_model.objects.filter(pk__in=[o.pk for o in self.blank]).count(), 2)
        self.assertEqual(opportunity_model.objects.get(pk=self.child.pk).parent_opportunity_id, self.keep.pk)
        self.assertEqual(list(apps.get_model("discovery", "AIAnalysis").objects.values_list("need", flat=True)), ["n2"])
        matches = apps.get_model("discovery", "OpportunityProductMatch").objects.filter(opportunity=keep)
        self.assertEqual(sorted(matches.values_list("product__name", "match_score")), [("p1", 0.9), ("p2", 0.7)])
        self.assertEqual(list(apps.get_model("discovery", "Evidence").objects.values_list("content", flat=True)), ["e2"])
        self.assertEqual(apps.get_model("discovery", "ProductOrder").objects.get().opportunity_id, self.keep.pk)
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "discovery_opportunity")
        self.assertIn("uniq_opportunity_business_engine_id", constraints)
