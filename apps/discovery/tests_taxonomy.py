import json
from unittest.mock import MagicMock, patch
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.cache import cache

from apps.businesses.models import Business
from apps.products.models import Category, Product
from apps.discovery.models import Opportunity, Customer, OpportunityProductMatch
from apps.discovery.ai_contracts import (
    CandidateInputSchema,
    ExtractedNeedSchema,
    FinalOpportunityAnalysisSchema,
)
from apps.discovery.llm_provider import MockLLMProvider
from apps.discovery.opportunity_service import OpportunityService
from apps.discovery.taxonomy.router import HierarchicalCategoryRouter
from apps.discovery.taxonomy.resolver import LLMAmbiguityResolver
from apps.discovery.taxonomy.product_matcher import BranchProductMatcher
from apps.discovery.taxonomy.cache import (
    get_seller_taxonomy,
    invalidate_seller_taxonomy_cache,
    get_taxonomy_cache_key,
)
from apps.discovery.taxonomy.normalizer import normalize_persian_text
from apps.discovery.taxonomy.cheap_filter import default_cheap_filter

User = get_user_model()


class HierarchicalTaxonomySuiteTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="tax_seller@example.com",
            email="tax_seller@example.com",
            password="StrongPassword123!"
        )
        self.business = Business.objects.create(
            user=self.user,
            name="فروشگاه جامع ایرانیان",
            business_type="PHYSICAL"
        )
        self.mock_llm = MockLLMProvider()

    # =========================================================================
    # 1. Exact Category Match
    # =========================================================================
    def test_01_exact_category_match(self):
        cat = Category.objects.create(
            business=self.business,
            name="کفش چرم طبیعی",
            product_type="PHYSICAL"
        )
        Product.objects.create(
            business=self.business,
            name="کفش چرم طبیعی مردانه تبریز",
            category=cat,
            price=1800000,
            status="ACTIVE",
            is_discovery_active=True
        )

        payload = {
            "source_platform": "telegram",
            "source_message_id": "tg_exact_01",
            "sender_username": "buyer_01",
            "source_raw_message": "سلام وقت بخیر کفش چرم طبیعی قیمت چنده؟ موجود دارید؟"
        }
        res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        self.assertEqual(res["status"], "MATCHED")
        self.assertIsNotNone(res["category"])
        self.assertEqual(res["category"]["id"], cat.id)
        self.assertGreaterEqual(res["category"]["confidence"], 0.85)
        self.assertTrue(len(res["products"]) > 0)
        self.assertEqual(res["products"][0]["id"], Product.objects.get(category=cat).id)

    # =========================================================================
    # 2. Persian Paraphrases
    # =========================================================================
    def test_02_persian_paraphrases(self):
        cat = Category.objects.create(
            business=self.business,
            name="لپ تاپ",
            keywords=["رایانه همراه", "نوت بوک", "سیستم پرتابل"],
            product_type="PHYSICAL"
        )
        Product.objects.create(
            business=self.business,
            name="لپ تاپ لنوو تینک پد",
            category=cat,
            price=35000000,
            status="ACTIVE",
            is_discovery_active=True
        )

        payload = {
            "source_platform": "x",
            "source_message_id": "x_paraphrase_02",
            "sender_username": "tech_geek",
            "source_raw_message": "دوستان برای خرید رایانه همراه خوب تا ۴۰ تومن چی پیشنهاد میدید؟"
        }
        res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["category"]["id"], cat.id)

    # =========================================================================
    # 3. Spelling Variations (Arabic Yeh/Kaf, Tanween, ZWNJ, Digits)
    # =========================================================================
    def test_03_spelling_variations(self):
        cat = Category.objects.create(
            business=self.business,
            name="کفش چرم",
            product_type="PHYSICAL"
        )
        Product.objects.create(
            business=self.business,
            name="کفش چرم اصل",
            category=cat,
            price=2000000,
            status="ACTIVE",
            is_discovery_active=True
        )

        # Using Arabic 'ك', Arabic 'ي', and Persian numerals: "سلام كفش چرم داريد؟ سايز ۴۲ ميخوام بخرم"
        payload = {
            "source_platform": "instagram",
            "source_message_id": "ig_spelling_03",
            "sender_username": "stylish_buyer",
            "source_raw_message": "سلام كفش چرم داريد؟ سايز ۴۲ ميخوام بخرم"
        }
        res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["category"]["id"], cat.id)

    # =========================================================================
    # 4. 1-Level Taxonomy
    # =========================================================================
    def test_04_single_level_taxonomy(self):
        cat = Category.objects.create(business=self.business, name="موبایل")
        Product.objects.create(
            business=self.business,
            name="گوشی سامسونگ A54",
            category=cat,
            price=15000000,
            status="ACTIVE",
            is_discovery_active=True
        )

        payload = {
            "source_platform": "divar",
            "source_message_id": "divar_lvl1_04",
            "sender_username": "phone_buyer",
            "source_raw_message": "خریدار موبایل سامسونگ تمیز و نو هستم قیمت بدید"
        }
        res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["category"]["id"], cat.id)
        opp = Opportunity.objects.get(id=res["opportunity_id"])
        self.assertEqual(opp.category.depth, 1)

    # =========================================================================
    # 5. 2-Level Taxonomy
    # =========================================================================
    def test_05_two_level_taxonomy(self):
        root = Category.objects.create(business=self.business, name="الکترونیک")
        sub = Category.objects.create(business=self.business, name="هدفون و هندزفری", parent=root)
        Product.objects.create(
            business=self.business,
            name="هدفون بی سیم سونی",
            category=sub,
            price=4500000,
            status="ACTIVE",
            is_discovery_active=True
        )

        payload = {
            "source_platform": "telegram",
            "source_message_id": "tg_lvl2_05",
            "sender_username": "music_lover",
            "source_raw_message": "قیمت هدفون و هندزفری بی سیم سونی چنده؟ قصد خرید دارم"
        }
        res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["category"]["id"], sub.id)
        self.assertEqual(sub.depth, 2)

    # =========================================================================
    # 6. 3-Level Taxonomy
    # =========================================================================
    def test_06_three_level_taxonomy(self):
        c1 = Category.objects.create(business=self.business, name="پوشاک")
        c2 = Category.objects.create(business=self.business, name="مردانه", parent=c1)
        c3 = Category.objects.create(business=self.business, name="کت و شلوار", parent=c2)
        Product.objects.create(
            business=self.business,
            name="کت و شلوار مجلسی مردانه",
            category=c3,
            price=5500000,
            status="ACTIVE",
            is_discovery_active=True
        )

        payload = {
            "source_platform": "x",
            "source_message_id": "x_lvl3_06",
            "sender_username": "gentleman",
            "source_raw_message": "سلام کت و شلوار مردانه مجلسی شیک از کجا بخرم؟"
        }
        res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["category"]["id"], c3.id)
        self.assertEqual(c3.depth, 3)

    # =========================================================================
    # 7. 4-Level Taxonomy
    # =========================================================================
    def test_07_four_level_taxonomy(self):
        c1 = Category.objects.create(business=self.business, name="دیجیتال")
        c2 = Category.objects.create(business=self.business, name="کامپیوتر", parent=c1)
        c3 = Category.objects.create(business=self.business, name="قطعات داخلی", parent=c2)
        c4 = Category.objects.create(business=self.business, name="کارت گرافیک", parent=c3)
        Product.objects.create(
            business=self.business,
            name="کارت گرافیک RTX 4070",
            category=c4,
            price=42000000,
            status="ACTIVE",
            is_discovery_active=True
        )

        payload = {
            "source_platform": "divar",
            "source_message_id": "divar_lvl4_07",
            "sender_username": "pc_builder",
            "source_raw_message": "سلام کارت گرافیک کامپیوتر RTX 4070 موجوده برای خرید؟"
        }
        res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["category"]["id"], c4.id)
        self.assertEqual(c4.depth, 4)

    # =========================================================================
    # 8. 5-Level Taxonomy
    # =========================================================================
    def test_08_five_level_taxonomy(self):
        c1 = Category.objects.create(business=self.business, name="کالا")
        c2 = Category.objects.create(business=self.business, name="دیجیتال", parent=c1)
        c3 = Category.objects.create(business=self.business, name="لپ تاپ", parent=c2)
        c4 = Category.objects.create(business=self.business, name="گیمینگ", parent=c3)
        c5 = Category.objects.create(business=self.business, name="ایسوس", parent=c4)
        Product.objects.create(
            business=self.business,
            name="لپ تاپ گیمینگ ایسوس مدل ROG Strix",
            category=c5,
            price=75000000,
            status="ACTIVE",
            is_discovery_active=True
        )

        payload = {
            "source_platform": "telegram",
            "source_message_id": "tg_lvl5_08",
            "sender_username": "gamer99",
            "source_raw_message": "سلام لپ تاپ گیمینگ ایسوس مدل جدید موجود دارید بخرم؟ قیمت چنده؟"
        }
        res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["category"]["id"], c5.id)
        self.assertEqual(c5.depth, 5)

    # =========================================================================
    # 9. Leaf Before Level 5
    # =========================================================================
    def test_09_leaf_before_level_5(self):
        # Tree with shallow leaf at depth 2 and deep branch at depth 4
        c1 = Category.objects.create(business=self.business, name="لوازم جانبی")
        leaf_d2 = Category.objects.create(business=self.business, name="ماوس و کیبورد", parent=c1)

        d_other = Category.objects.create(business=self.business, name="دیجیتال")
        d_laptop = Category.objects.create(business=self.business, name="نوت بوک", parent=d_other)
        d_part = Category.objects.create(business=self.business, name="رم و هارد", parent=d_laptop)

        Product.objects.create(
            business=self.business,
            name="ماوس و کیبورد گیمینگ بی سیم",
            category=leaf_d2,
            price=950000,
            status="ACTIVE",
            is_discovery_active=True
        )

        payload = {
            "source_platform": "x",
            "source_message_id": "x_leaf_09",
            "sender_username": "accessory_user",
            "source_raw_message": "دنبال خرید ماوس و کیبورد بی سیم با کیفیت هستم، موجود دارید؟"
        }
        res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["category"]["id"], leaf_d2.id)
        self.assertEqual(leaf_d2.depth, 2)

    # =========================================================================
    # 10. Ambiguous Category Triggers Resolver
    # =========================================================================
    def test_10_ambiguous_category_resolution(self):
        root = Category.objects.create(business=self.business, name="پوشاک")
        cand1 = Category.objects.create(
            business=self.business,
            name="شلوار کتان مردانه",
            parent=root,
            keywords=["کتان", "شلوار رسمی"]
        )
        cand2 = Category.objects.create(
            business=self.business,
            name="شلوار جین مردانه",
            parent=root,
            keywords=["جین", "لی", "اسپرت"]
        )

        Product.objects.create(
            business=self.business,
            name="شلوار کتان مردانه کلاسیک",
            category=cand1,
            price=600000,
            status="ACTIVE",
            is_discovery_active=True
        )
        Product.objects.create(
            business=self.business,
            name="شلوار جین مردانه کلاسیک",
            category=cand2,
            price=650000,
            status="ACTIVE",
            is_discovery_active=True
        )

        # Mock resolver explicitly called to verify integration
        mock_provider = MagicMock(spec=MockLLMProvider)
        mock_provider.extract_need.return_value = ExtractedNeedSchema(
            is_potential_buyer=True,
            need="شلوار مردانه کلاسیک میخوام بخرم",
            product_type="PHYSICAL",
            attributes={"نوع": "کتان"},
            urgency="high",
            intent_score=0.90,
            confidence=0.95
        )
        mock_provider.resolve_category_ambiguity.return_value = {
            "selected_category_id": cand1.id,
            "confidence": 0.94,
            "reason": "تطابق واژه کتان با دسته‌بندی کتان"
        }
        mock_provider.analyze_final_opportunity.return_value = FinalOpportunityAnalysisSchema(
            is_real_opportunity=True,
            intent_score=0.90,
            need_summary="شلوار کتان",
            reason="تطابق دقیق",
            recommended_product_ids=[Product.objects.get(category=cand1).id],
            product_reasons={},
            suggested_reply="سلام، شلوار کتان موجود است.",
            confidence=0.95
        )

        payload = {
            "source_platform": "telegram",
            "source_message_id": "tg_ambig_10",
            "sender_username": "buyer_ambig",
            "source_raw_message": "سلام شلوار مردانه کلاسیک موجود دارید؟ میخوام بخرم قیمت چنده؟"
        }
        res = OpportunityService.process_candidate(payload, self.business, mock_provider)

        self.assertEqual(res["status"], "MATCHED")
        self.assertEqual(res["category"]["id"], cand1.id)
        mock_provider.resolve_category_ambiguity.assert_called_once()

    # =========================================================================
    # 11. Unknown Category Abstention (CATEGORY_UNCERTAIN)
    # =========================================================================
    def test_11_unknown_category_abstention(self):
        Category.objects.create(business=self.business, name="کفش ورزشی")

        payload = {
            "source_platform": "divar",
            "source_message_id": "divar_unknown_11",
            "sender_username": "car_buyer",
            "source_raw_message": "خریدار فوری ماشین پراید مدل ۹۳ دوگانه سوز هستم نقد پرداخت میکنم"
        }
        res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        self.assertEqual(res["status"], "CATEGORY_UNCERTAIN")
        self.assertIsNone(res["category"])
        self.assertEqual(len(res["products"]), 0)

        opp = Opportunity.objects.get(source_message_id="divar_unknown_11")
        self.assertEqual(opp.status, "CATEGORY_UNCERTAIN")

    # =========================================================================
    # 12. Hallucinated Category ID Rejection
    # =========================================================================
    def test_12_hallucinated_category_id_rejection(self):
        candidates = [
            {"id": 10, "name": "گوشی", "full_path": "گوشی", "keywords": []},
            {"id": 20, "name": "تبلت", "full_path": "تبلت", "keywords": []},
        ]
        mock_provider = MagicMock()
        # LLM returns a hallucinated ID: 99999
        mock_provider.resolve_category_ambiguity.return_value = {
            "selected_category_id": 99999,
            "confidence": 0.99,
            "reason": "Hallucinated id"
        }

        selected_id, conf, reason = LLMAmbiguityResolver.resolve_ambiguity(
            customer_need="خرید تبلت",
            candidates=candidates,
            llm_provider=mock_provider
        )

        # Must reject hallucinated category ID
        self.assertIsNone(selected_id)
        self.assertIn("REJECTED_HALLUCINATED_CATEGORY_ID", reason)

    # =========================================================================
    # 13. Hallucinated Product ID Rejection
    # =========================================================================
    def test_13_hallucinated_product_id_rejection(self):
        cat = Category.objects.create(business=self.business, name="ساعت هوشمند")
        valid_prod = Product.objects.create(
            business=self.business,
            name="ساعت هوشمند شیائومی",
            category=cat,
            price=1200000,
            status="ACTIVE",
            is_discovery_active=True
        )

        mock_provider = MagicMock(spec=MockLLMProvider)
        mock_provider.extract_need.return_value = ExtractedNeedSchema(
            is_potential_buyer=True,
            need="ساعت هوشمند شیائومی میخوام بخرم",
            product_type="PHYSICAL",
            attributes={},
            urgency="high",
            intent_score=0.90,
            confidence=0.95
        )
        # LLM tries to recommend non-existent product ID 888888 along with valid product
        mock_provider.analyze_final_opportunity.return_value = FinalOpportunityAnalysisSchema(
            is_real_opportunity=True,
            intent_score=0.90,
            need_summary="ساعت هوشمند",
            reason="تطابق",
            recommended_product_ids=[888888, valid_prod.id],
            product_reasons={888888: "Fake", valid_prod.id: "Real"},
            suggested_reply="موجود است.",
            confidence=0.95
        )

        payload = {
            "source_platform": "instagram",
            "source_message_id": "ig_halluc_prod_13",
            "sender_username": "watch_fan",
            "source_raw_message": "قیمت ساعت هوشمند شیائومی چنده؟ میخوام بخرم"
        }
        res = OpportunityService.process_candidate(payload, self.business, mock_provider)

        self.assertEqual(res["status"], "MATCHED")
        opp = Opportunity.objects.get(id=res["opportunity_id"])
        # In DB, only legitimate business product must be saved
        saved_match_ids = list(opp.product_matches.values_list("product_id", flat=True))
        self.assertIn(valid_prod.id, saved_match_ids)
        self.assertNotIn(888888, saved_match_ids)

    # =========================================================================
    # 14. Duplicate Message Handling
    # =========================================================================
    def test_14_duplicate_message_handling(self):
        cat = Category.objects.create(business=self.business, name="کیف زنانه")
        Product.objects.create(
            business=self.business,
            name="کیف دستی زنانه چرم",
            category=cat,
            price=850000,
            status="ACTIVE",
            is_discovery_active=True
        )

        payload = {
            "source_platform": "telegram",
            "source_message_id": "tg_dup_14",
            "sender_username": "repeat_buyer",
            "source_raw_message": "سلام کیف زنانه چرم قیمت چنده؟ میخوام بخرم"
        }

        res1 = OpportunityService.process_candidate(payload, self.business, self.mock_llm)
        self.assertEqual(res1["status"], "MATCHED")

        # Second submission
        res2 = OpportunityService.process_candidate(payload, self.business, self.mock_llm)
        self.assertEqual(res2["status"], "DUPLICATE")
        self.assertEqual(res2["opportunity_id"], res1["opportunity_id"])
        self.assertEqual(Opportunity.objects.filter(source_message_id="tg_dup_14").count(), 1)

    # =========================================================================
    # 15. Taxonomy Cache Hit
    # =========================================================================
    def test_15_taxonomy_cache_hit(self):
        Category.objects.create(business=self.business, name="عینک آفتابی")

        # First call loads and caches
        tax1 = get_seller_taxonomy(self.business)
        cache_key = get_taxonomy_cache_key(self.business.id, self.business.taxonomy_version)
        self.assertIsNotNone(cache.get(cache_key))

        # Second call returns identical structure from cache
        tax2 = get_seller_taxonomy(self.business)
        self.assertEqual(tax1["taxonomy_version"], tax2["taxonomy_version"])
        self.assertEqual(len(tax1["roots"]), len(tax2["roots"]))

    # =========================================================================
    # 16. Cache Invalidation on Taxonomy Update
    # =========================================================================
    def test_16_cache_invalidation_on_update(self):
        c1 = Category.objects.create(business=self.business, name="پوشاک ورزشی")
        tax1 = get_seller_taxonomy(self.business)
        self.assertEqual(len(tax1["roots"]), 1)

        # Add new root category -> Category.save automatically invalidates cache
        c2 = Category.objects.create(business=self.business, name="تجهیزات کوهنوردی")
        self.business.refresh_from_db()

        tax2 = get_seller_taxonomy(self.business)
        self.assertEqual(len(tax2["roots"]), 2)
        root_names = [r["name"] for r in tax2["roots"]]
        self.assertIn("پوشاک ورزشی", root_names)
        self.assertIn("تجهیزات کوهنوردی", root_names)

    # =========================================================================
    # 17. Seller Isolation
    # =========================================================================
    def test_17_seller_isolation(self):
        user_b = User.objects.create_user(username="seller_b@example.com", password="Pass123!")
        business_b = Business.objects.create(user=user_b, name="فروشگاه بی")

        cat_a = Category.objects.create(business=self.business, name="فرش دستباف")
        cat_b = Category.objects.create(business=business_b, name="فرش ماشینی کاشان")

        prod_b = Product.objects.create(
            business=business_b,
            name="فرش ۱۲ متری کاشان",
            category=cat_b,
            price=12000000,
            status="ACTIVE",
            is_discovery_active=True
        )

        # Message matching فرش ماشینی sent to Business A
        payload = {
            "source_platform": "x",
            "source_message_id": "x_isol_17",
            "sender_username": "rug_lover",
            "source_raw_message": "سلام خریدار فرش ماشینی کاشان ۱۲ متری هستم قیمت چنده؟"
        }
        res_a = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

        # Products belonging to Business B must NEVER appear in Business A's result
        for p in res_a["products"]:
            self.assertNotEqual(p["id"], prod_b.id)

    # =========================================================================
    # 18. Product Branch Isolation
    # =========================================================================
    def test_18_product_branch_isolation(self):
        root = Category.objects.create(business=self.business, name="پوشاک")
        branch_men = Category.objects.create(business=self.business, name="مردانه", parent=root)
        branch_women = Category.objects.create(business=self.business, name="زنانه", parent=root)

        prod_men = Product.objects.create(
            business=self.business,
            name="پیراهن آستین بلند نخی",
            category=branch_men,
            price=450000,
            status="ACTIVE",
            is_discovery_active=True
        )
        prod_women = Product.objects.create(
            business=self.business,
            name="پیراهن مجلسی زنانه حریر",
            category=branch_women,
            price=950000,
            status="ACTIVE",
            is_discovery_active=True
        )

        # Match strictly inside branch_women
        matched = BranchProductMatcher.match_products(
            business=self.business,
            category=branch_women,
            need_text="پیراهن مجلسی زنانه میخوام بخرم",
            need_embedding=[0.1] * 128
        )

        matched_ids = [m["product_id"] for m in matched]
        self.assertIn(prod_women.id, matched_ids)
        self.assertNotIn(prod_men.id, matched_ids)

    # =========================================================================
    # 19. Non-Buyer Message Rejection
    # =========================================================================
    def test_19_non_buyer_message_rejection(self):
        Category.objects.create(business=self.business, name="محصولات دیجیتال")

        non_buyer_messages = [
            ("tg_nb_1", "سلام روزتون بخیر خسته نباشید"),
            ("x_nb_2", "امروز هوا چقدر خوبه بریم پیاده‌روی"),
            ("ig_nb_3", "شاخص کل بورس امروز منفی شد متاسفانه"),
        ]

        for msg_id, raw_msg in non_buyer_messages:
            payload = {
                "source_platform": "telegram",
                "source_message_id": msg_id,
                "sender_username": "casual_user",
                "source_raw_message": raw_msg
            }
            res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

            self.assertEqual(res["status"], "NOT_A_BUYER")
            self.assertIsNone(res["opportunity_id"])
            self.assertEqual(len(res["products"]), 0)

    # =========================================================================
    # 20. Multiple Product Ranking
    # =========================================================================
    def test_20_multiple_product_ranking(self):
        cat = Category.objects.create(business=self.business, name="لپ تاپ مهندسی")
        p_high = Product.objects.create(
            business=self.business,
            name="لپ تاپ دل مهندسی با رم 32 گیگ",
            category=cat,
            description="مناسب کارهای سنگین مهندسی و شبیه‌سازی",
            attributes={"رم": "32 گیگ", "پردازنده": "i7"},
            price=55000000,
            status="ACTIVE",
            is_discovery_active=True,
            discovery_priority=5
        )
        p_mid = Product.objects.create(
            business=self.business,
            name="لپ تاپ لنوو معمولی",
            category=cat,
            description="لپ تاپ مناسب کارهای روزمره اداری",
            attributes={"رم": "8 گیگ"},
            price=25000000,
            status="ACTIVE",
            is_discovery_active=True,
            discovery_priority=2
        )

        matched = BranchProductMatcher.match_products(
            business=self.business,
            category=cat,
            need_text="لپ تاپ مهندسی با رم 32 گیگ میخوام بخرم برای شبیه سازی",
            need_embedding=[0.2] * 128,
            extracted_attributes={"رم": "32 گیگ"}
        )

        self.assertEqual(len(matched), 2)
        # Highest match must be rank 1
        self.assertEqual(matched[0]["product_id"], p_high.id)
        self.assertEqual(matched[0]["rank"], 1)
        self.assertGreater(matched[0]["score"], matched[1]["score"])
        self.assertIn("همخوانی ویژگی‌ها", matched[0]["reason"])

    # =========================================================================
    # 21. Canonical Input Across All 4 Platforms
    # =========================================================================
    def test_21_canonical_input_all_four_platforms(self):
        cat = Category.objects.create(business=self.business, name="تجهیزات ورزشی")
        prod = Product.objects.create(
            business=self.business,
            name="مت یوگا ضخیم",
            category=cat,
            price=320000,
            status="ACTIVE",
            is_discovery_active=True
        )

        platforms = [
            ("telegram", "tg_canon_1", "@yoga_fan", "مت یوگا با کیفیت موجود دارید؟ قصد خرید دارم"),
            ("x", "x_canon_2", "fitness_guy", "دنبال مت یوگا خوب هستم برای تمرین، قیمت چنده؟"),
            ("instagram", "ig_canon_3", "sport_lover_99", "سلام مت یوگا ضخیم میخوام سفارش بدم لطفا راهنمایی کنید"),
            ("divar", "divar_canon_4", "buyer_divar", "سلام خریدار مت یوگا هستم قیمت تخفیف داره؟"),
        ]

        for platform, msg_id, sender, raw_text in platforms:
            payload = {
                "source_platform": platform,
                "source_message_id": msg_id,
                "sender_username": sender,
                "source_raw_message": raw_text
            }
            res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

            self.assertEqual(res["status"], "MATCHED")
            self.assertEqual(res["customer"]["platform"], platform)
            self.assertEqual(res["customer"]["username"], sender.lstrip("@"))
            self.assertEqual(res["category"]["id"], cat.id)
            self.assertEqual(res["products"][0]["id"], prod.id)

            opp = Opportunity.objects.get(id=res["opportunity_id"])
            self.assertEqual(opp.source_platform, platform)
            self.assertEqual(opp.source_message_id, msg_id)

    # =========================================================================
    # 22. Golden Dataset Evaluation
    # =========================================================================
    def test_22_golden_dataset_evaluation(self):
        """
        Runs a golden dataset of varied buyer queries, non-buyer queries, and out-of-domain queries.
        Asserts >= 90% routing accuracy on in-domain buyers, and 100% rejection on non-buyers.
        """
        c_coffee = Category.objects.create(business=self.business, name="قهوه ساز")
        c_grinder = Category.objects.create(business=self.business, name="آسیاب قهوه")

        Product.objects.create(
            business=self.business,
            name="اسپرسوساز نوا 149",
            category=c_coffee,
            price=6200000,
            status="ACTIVE",
            is_discovery_active=True
        )
        Product.objects.create(
            business=self.business,
            name="آسیاب قهوه باراتزا",
            category=c_grinder,
            price=4800000,
            status="ACTIVE",
            is_discovery_active=True
        )

        golden_samples = [
            # In-domain buyers (Expect MATCHED to specific category)
            {"msg": "اسپرسوساز و قهوه ساز خانگی خوب چی بخرم قیمت مناسب؟", "type": "BUYER", "expected_cat": c_coffee.id},
            {"msg": "قیمت قهوه ساز نوا چنده؟ موجود دارید بخرم؟", "type": "BUYER", "expected_cat": c_coffee.id},
            {"msg": "دنبال خرید آسیاب قهوه برقی با تیغه مخروطی هستم", "type": "BUYER", "expected_cat": c_grinder.id},
            {"msg": "آسیاب قهوه با درجه بندی دقیق میخوام سفارش بدم", "type": "BUYER", "expected_cat": c_grinder.id},
            # Non-buyers (Expect NOT_A_BUYER)
            {"msg": "سلام روز خوش", "type": "NON_BUYER", "expected_cat": None},
            {"msg": "چرا بارون نمیاد امسال؟", "type": "NON_BUYER", "expected_cat": None},
            # Out-of-domain queries (Expect CATEGORY_UNCERTAIN)
            {"msg": "خریدار لاستیک پراید بارز صفر هستم نقد", "type": "OUT_OF_DOMAIN", "expected_cat": None},
            {"msg": "بلیت کنسرت همایون شجریان خریدارم کسی داره؟", "type": "OUT_OF_DOMAIN", "expected_cat": None},
        ]

        correct_buyer_routes = 0
        total_buyers = 0
        correct_non_buyer_rejects = 0
        total_non_buyers = 0
        correct_out_domain_abstentions = 0
        total_out_domain = 0

        for idx, item in enumerate(golden_samples, start=1):
            payload = {
                "source_platform": "telegram",
                "source_message_id": f"golden_msg_{idx}",
                "sender_username": f"user_{idx}",
                "source_raw_message": item["msg"]
            }
            res = OpportunityService.process_candidate(payload, self.business, self.mock_llm)

            if item["type"] == "BUYER":
                total_buyers += 1
                if res["status"] == "MATCHED" and res.get("category") and res["category"]["id"] == item["expected_cat"]:
                    correct_buyer_routes += 1
            elif item["type"] == "NON_BUYER":
                total_non_buyers += 1
                if res["status"] == "NOT_A_BUYER":
                    correct_non_buyer_rejects += 1
            elif item["type"] == "OUT_OF_DOMAIN":
                total_out_domain += 1
                if res["status"] == "CATEGORY_UNCERTAIN":
                    correct_out_domain_abstentions += 1

        buyer_accuracy = correct_buyer_routes / total_buyers
        non_buyer_accuracy = correct_non_buyer_rejects / total_non_buyers
        out_domain_accuracy = correct_out_domain_abstentions / total_out_domain

        self.assertGreaterEqual(buyer_accuracy, 0.90, f"Buyer accuracy was {buyer_accuracy:.2%}")
        self.assertEqual(non_buyer_accuracy, 1.0, f"Non-buyer rejection accuracy was {non_buyer_accuracy:.2%}")
        self.assertEqual(out_domain_accuracy, 1.0, f"Out-of-domain abstention accuracy was {out_domain_accuracy:.2%}")
