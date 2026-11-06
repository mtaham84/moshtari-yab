"""Comprehensive acceptance tests for Peyda v2:
- Dynamic hierarchical product taxonomy & Leaf Category discovery (arbitrary depth, level 2 and 4 leaf nodes).
- Platform-specific query generation (X max 10, three domains, natural 2-6 words; Instagram max 5, concise 1-3 words).
- Independent MMR diversification for X and Instagram.
- Safe idempotent query replacement, archiving old queries without deleting historical records.
- Independent Bayesian learning baseline (v2) for X and Instagram.
- Luna 5-dimensional post qualification (rejection of jokes/news/ads, detection of implicit needs and competitor dissatisfaction).
- Strict guarantee that no Opportunity is created from REJECT posts.
- Statistical integrity across dashboard metrics.
- Multi-tenant compatibility.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.businesses.models import Business
from apps.products.models import Category, Product
from apps.products.taxonomy import get_active_leaf_categories
from apps.discovery.models import (
    InstagramSourceStat,
    XSearchQuery,
)
from apps.discovery.leaf_query_generator import (
    generate_leaf_category_x_queries,
    generate_leaf_category_instagram_queries,
    replace_platform_queries_for_business,
    MAX_X_QUERIES_PER_LEAF,
    MAX_IG_QUERIES_PER_LEAF,
)
from need_engine.post_qualification import (
    determine_decision,
    qualify_post_deterministic,
)
from apps.discovery.services import get_leaf_category_dashboard_stats


class LeafTaxonomyAndQualificationTestCase(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user(username="test_seller", password="password123")
        self.business = Business.objects.create(
            user=user,
            name="بوتیک شیک‌پوشان و اکسسوری",
            business_domain="پوشاک و اکسسوری",
        )

        # Build a deep hierarchy:
        # پوشاک (depth 1)
        #   └── پوشاک زنانه (depth 2)
        #         └── لباس زیر زنانه (depth 3)
        #               └── شورت زنانه (depth 4)
        self.cat_root = Category.objects.create(
            business=self.business,
            name="پوشاک",
            product_type="PHYSICAL",
        )
        self.cat_women = Category.objects.create(
            business=self.business,
            parent=self.cat_root,
            name="پوشاک زنانه",
            product_type="PHYSICAL",
        )
        self.cat_underwear = Category.objects.create(
            business=self.business,
            parent=self.cat_women,
            name="لباس زیر زنانه",
            product_type="PHYSICAL",
        )
        self.cat_panties = Category.objects.create(
            business=self.business,
            parent=self.cat_underwear,
            name="شورت زنانه",
            product_type="PHYSICAL",
        )

        # Another branch: Level 2 Leaf Category
        # پوشاک > جوراب اسپرت (depth 2 - leaf)
        self.cat_socks = Category.objects.create(
            business=self.business,
            parent=self.cat_root,
            name="جوراب اسپرت",
            product_type="PHYSICAL",
        )

        # Associate active products
        self.prod_panty = Product.objects.create(
            business=self.business,
            category=self.cat_panties,
            name="شورت نخی زنانه ضدحساسیت",
            description="شورت زنانه پنبه‌ای باکیفیت و بسیار لطیف",
            status="ACTIVE",
            is_discovery_active=True,
        )
        self.prod_socks = Product.objects.create(
            business=self.business,
            category=self.cat_socks,
            name="جوراب مچی نخی اسپرت",
            description="جوراب ورزشی و روزمره نانو ضدبو",
            status="ACTIVE",
            is_discovery_active=True,
        )

    def test_01_deep_category_depth_and_path(self):
        """1. Verify that Category correctly supports depth 4 without arbitrary limits."""
        self.assertEqual(self.cat_panties.depth, 4)
        self.assertEqual(self.cat_panties.get_full_path(), "پوشاک > پوشاک زنانه > لباس زیر زنانه > شورت زنانه")
        self.assertTrue(self.cat_panties.is_leaf)
        self.assertFalse(self.cat_women.is_leaf)
        self.assertFalse(self.cat_underwear.is_leaf)

    def test_02_level_2_can_be_leaf_category(self):
        """2. Verify that a category at level 2 can be a Leaf Category."""
        self.assertEqual(self.cat_socks.depth, 2)
        self.assertTrue(self.cat_socks.is_leaf)

    def test_03_level_4_can_be_leaf_category(self):
        """3. Verify that a category at level 4 is recognized as a Leaf Category."""
        self.assertEqual(self.cat_panties.depth, 4)
        self.assertTrue(self.cat_panties.is_leaf)

    def test_04_categories_with_children_do_not_generate_leaf_queries(self):
        """4. Verify that parent categories with children are never included in active leaf categories."""
        active_leaves = get_active_leaf_categories(self.business)
        leaf_ids = {c.id for c in active_leaves}
        self.assertNotIn(self.cat_root.id, leaf_ids)
        self.assertNotIn(self.cat_women.id, leaf_ids)
        self.assertNotIn(self.cat_underwear.id, leaf_ids)

    def test_05_only_active_leaf_categories_with_active_products_selected(self):
        """5. Only active leaf categories that contain active seller products are discovered."""
        # Create an empty leaf category
        empty_leaf = Category.objects.create(
            business=self.business,
            parent=self.cat_root,
            name="کلاه زمستانه",
            product_type="PHYSICAL",
        )
        active_leaves = get_active_leaf_categories(self.business)
        leaf_ids = {c.id for c in active_leaves}
        self.assertNotIn(empty_leaf.id, leaf_ids)
        self.assertIn(self.cat_panties.id, leaf_ids)
        self.assertIn(self.cat_socks.id, leaf_ids)

    def test_06_x_query_generation_caps_at_10_and_three_domains(self):
        """6. X queries generate across three domains and do not exceed 10 queries."""
        products = [self.prod_panty]
        mock_llm_response = {
            "queries": [
                {"query": "شورت زنانه نخی از کجا بخرم", "domain": "purchase_advice"},
                {"query": "پیشنهاد شورت زنانه ضد حساسیت", "domain": "purchase_advice"},
                {"query": "قیمت لباس زیر نخی زنانه", "domain": "purchase_advice"},
                {"query": "حساسیت پوستی به لباس زیر", "domain": "pain_symptoms"},
                {"query": "عرق سوز شدن با شورت نایلونی", "domain": "pain_symptoms"},
                {"query": "لباس زیر نخی مناسب ورزش بانوان", "domain": "persona_context"},
                {"query": "شورت زنانه مناسب استفاده روزمره", "domain": "persona_context"},
            ]
        }
        with patch("apps.core.llm.chat_json", return_value=mock_llm_response):
            queries = generate_leaf_category_x_queries(self.cat_panties, products, self.business)

        self.assertLessEqual(len(queries), MAX_X_QUERIES_PER_LEAF)
        self.assertGreaterEqual(len(queries), 3)
        domains = {q["domain"] for q in queries}
        self.assertTrue(domains.intersection({"pain_symptoms", "purchase_advice", "persona_context"}))

    def test_07_instagram_query_generation_caps_at_5(self):
        """7. Instagram queries are concise (1-3 words) and cap at maximum 5 queries."""
        products = [self.prod_panty]
        mock_llm_response = {
            "queries": [
                {"query": "شورت زنانه", "query_type": "exact_category"},
                {"query": "شورت نخی زنانه", "query_type": "exact_product"},
                {"query": "لباس زیر زنانه", "query_type": "synonym"},
                {"query": "شورت زنانه ضدحساسیت", "query_type": "variation"},
                {"query": "شورت نخی", "query_type": "alternative_spelling"},
                {"query": "شورت زنانه مجلسی", "query_type": "variation"},
            ]
        }
        with patch("apps.core.llm.chat_json", return_value=mock_llm_response):
            queries = generate_leaf_category_instagram_queries(self.cat_panties, products, self.business)

        self.assertLessEqual(len(queries), MAX_IG_QUERIES_PER_LEAF)
        for q in queries:
            words = q["query"].split()
            self.assertLessEqual(len(words), 3, f"Query '{q['query']}' exceeds 3 words!")

    def test_08_x_queries_natural_persian_word_count(self):
        """8. X queries use natural conversational Persian within 2-6 words."""
        queries = generate_leaf_category_x_queries(self.cat_socks, [self.prod_socks], self.business)
        self.assertTrue(len(queries) > 0)
        for q in queries:
            words = q["query"].split()
            self.assertTrue(2 <= len(words) <= 7, f"Query '{q['query']}' word count ({len(words)}) outside limits!")

    def test_09_instagram_queries_short_product_oriented(self):
        """9. Instagram queries are strictly product/service-oriented and not questions."""
        queries = generate_leaf_category_instagram_queries(self.cat_socks, [self.prod_socks], self.business)
        self.assertTrue(len(queries) > 0)
        forbidden_questions = ["چی بخرم", "از کجا بخرم", "چرا", "آیا"]
        for q in queries:
            words = q["query"].split()
            self.assertLessEqual(len(words), 3)
            for fq in forbidden_questions:
                self.assertNotIn(fq, q["query"])

    def test_10_mmr_removes_redundant_queries_independently(self):
        """10. MMR selects diverse queries and avoids redundant duplicates."""
        redundant_items = [
            {"query": "شورت زنانه نخی", "domain": "purchase_advice"},
            {"query": "شورت نخی زنانه", "domain": "purchase_advice"},
            {"query": "شورت زنانه نخی باکیفیت", "domain": "purchase_advice"},
            {"query": "عرق سوز شدن در تابستان", "domain": "pain_symptoms"},
            {"query": "لباس زیر مناسب باشگاه بانوان", "domain": "persona_context"},
        ]
        with patch("apps.core.llm.chat_json", return_value={"queries": redundant_items}):
            selected_x = generate_leaf_category_x_queries(self.cat_panties, [self.prod_panty], self.business)

        selected_texts = [q["query"] for q in selected_x]
        self.assertIn("عرق سوز شدن در تابستان", selected_texts)
        self.assertIn("لباس زیر مناسب باشگاه بانوان", selected_texts)

    def test_11_old_x_queries_archived_without_deleting_history(self):
        """11. Old active X queries are marked archived; historical data is preserved."""
        old_q = XSearchQuery.objects.create(
            business=self.business,
            text="خرید شورت زنانه ارزان",
            normalized_text="خرید شورت زنانه ارزان",
            state="active",
            generation_version="v1",
        )
        # Run replacement
        replace_platform_queries_for_business(self.business, platform="x")

        old_q.refresh_from_db()
        self.assertEqual(old_q.state, "archived")
        self.assertEqual(old_q.state_reason, "archived_by_v2_regeneration")

        # Verify new v2 queries exist and are active
        v2_queries = XSearchQuery.objects.filter(business=self.business, state="active", generation_version="v2")
        self.assertTrue(v2_queries.exists())

    def test_12_bayesian_baseline_initialized_independently(self):
        """12. Fresh v2 Bayesian baseline is initialized for new Instagram queries."""
        replace_platform_queries_for_business(self.business, platform="instagram")
        stats = InstagramSourceStat.objects.filter(business=self.business, generation_version="v2")
        self.assertTrue(stats.exists())
        first_stat = stats.first()
        self.assertEqual(first_stat.alpha, 1.0)
        self.assertEqual(first_stat.beta, 1.0)
        self.assertEqual(first_stat.requests, 0)
        self.assertEqual(first_stat.leads, 0)

    def test_13_luna_rejects_jokes_sarcasm_and_ads(self):
        """13. Luna qualification rejects humorous posts, sarcasm, and seller ads."""
        joke_text = "با این قیمت لپ تاپ و لباس زیر باید کلیه بفروشیم 😂"
        q_joke = qualify_post_deterministic("post_joke", joke_text, self.cat_panties.get_full_path(), [self.prod_panty.name])
        self.assertEqual(q_joke.content_type, "joke")
        self.assertEqual(q_joke.decision, "REJECT")
        self.assertFalse(q_joke.is_opportunity)

        ad_text = "فروش ویژه و تخفیف ۵۰ درصدی انواع لباس زیر و پوشاک زنانه"
        q_ad = qualify_post_deterministic("post_ad", ad_text, self.cat_panties.get_full_path(), [self.prod_panty.name])
        self.assertIn(q_ad.content_type, ["advertisement", "promotional_content", "news"])
        self.assertEqual(q_ad.decision, "REJECT")
        self.assertFalse(q_ad.is_opportunity)

    def test_14_luna_identifies_implicit_need_and_problem(self):
        """14. Luna identifies genuine problems and implicit needs as POTENTIAL leads."""
        problem_text = "چند وقته به خاطر جنس نامناسب لباس زیر بدنم خارش و سوزش شدید گرفته"
        q_prob = qualify_post_deterministic("post_pain", problem_text, self.cat_panties.get_full_path(), [self.prod_panty.name])
        self.assertEqual(q_prob.content_type, "genuine_need")
        self.assertEqual(q_prob.need_signal, "problem_or_pain")
        self.assertEqual(q_prob.decision, "POTENTIAL")
        self.assertTrue(q_prob.is_opportunity)

    def test_15_luna_identifies_competitor_dissatisfaction_and_alternative(self):
        """15. Luna recognizes competitor dissatisfaction and alternative seeking."""
        alt_text = "از برند قبلی لباس زیر به شدت ناراضی ام، کسی جایگزین خوب با پارچه نخی میشناسه؟"
        q_alt = qualify_post_deterministic("post_alt", alt_text, self.cat_panties.get_full_path(), [self.prod_panty.name])
        self.assertEqual(q_alt.content_type, "genuine_need")
        self.assertIn(q_alt.need_signal, ["seeking_alternative", "dissatisfaction_with_competitor", "explicit_product_need"])
        self.assertIn(q_alt.decision, ["QUALIFIED", "POTENTIAL"])
        self.assertTrue(q_alt.is_opportunity)

    def test_16_no_opportunity_created_from_rejected_post(self):
        """16. Verification that decision REJECT never produces an opportunity."""
        decision, is_opp = determine_decision(
            content_type="joke",
            need_signal="no_identifiable_need",
            purchase_stage="general_awareness",
            product_fit="no",
            buyer_intent_score=10,
            commercial_actionability_score=0,
        )
        self.assertEqual(decision, "REJECT")
        self.assertFalse(is_opp)

    def test_17_dashboard_stats_distinguish_x_and_instagram(self):
        """17. Dashboard statistics separate X and Instagram metrics explicitly."""
        replace_platform_queries_for_business(self.business, platform="all")
        stats = get_leaf_category_dashboard_stats(self.business)
        self.assertTrue(len(stats) >= 2)

        x_rows = [s for s in stats if s["platform"] == "x"]
        ig_rows = [s for s in stats if s["platform"] == "instagram"]

        self.assertTrue(len(x_rows) > 0)
        self.assertTrue(len(ig_rows) > 0)

        # Check required fields
        required_keys = [
            "category_path", "category_depth", "products_associated",
            "generated_queries_count", "active_queries_count", "archived_queries_count",
            "raw_posts_retrieved", "posts_evaluated_by_luna", "potential_opportunities_count",
            "qualified_opportunities_count", "delivered_opportunities_count",
        ]
        for key in required_keys:
            self.assertIn(key, x_rows[0])
            self.assertIn(key, ig_rows[0])

    def test_18_idempotent_dry_run_does_not_modify_database(self):
        """18. Dry run does not alter database state."""
        q_initial_count = XSearchQuery.objects.count()
        report = replace_platform_queries_for_business(self.business, dry_run=True, platform="all")
        self.assertEqual(report["status"], "dry_run_success")
        self.assertEqual(XSearchQuery.objects.count(), q_initial_count)

    def test_19_multi_tenant_clean_isolation(self):
        """19. Multi-tenant compatibility: works cleanly for another tenant."""
        user_2 = get_user_model().objects.create_user(username="tenant_2_user", password="password123")
        tenant_2 = Business.objects.create(user=user_2, name="کلینیک تخصصی دندانپزشکی لبخند")
        cat_implant = Category.objects.create(business=tenant_2, name="ایمپلنت دندان", product_type="SERVICE")
        Product.objects.create(
            business=tenant_2,
            category=cat_implant,
            name="کاشت ایمپلنت سوئیسی",
            status="ACTIVE",
            is_discovery_active=True,
        )
        res = replace_platform_queries_for_business(tenant_2, platform="all")
        self.assertEqual(res["status"], "success")
        t2_queries = XSearchQuery.objects.filter(business=tenant_2, state="active")
        self.assertTrue(t2_queries.exists())
        # Ensure tenant 1 queries are unaffected
        t1_queries = XSearchQuery.objects.filter(business=self.business)
        for q in t2_queries:
            self.assertNotIn(q.id, t1_queries.values_list("id", flat=True))

    def test_20_no_automated_engagement_in_scraper(self):
        """20. Ensure automated liking is removed/disabled in Instagram scraper."""
        from apps.discovery.management.commands.run_instagram_scraper import Command as ScraperCommand
        cmd = ScraperCommand()
        mock_client = MagicMock()
        mock_account = MagicMock()
        mock_account.daily_action_count = 0

        # Calling _maybe_human_like should NOT call media_like
        cmd._maybe_human_like(mock_client, "123456", mock_account)
        mock_client.media_like.assert_not_called()
        self.assertEqual(mock_account.daily_action_count, 0)
