import os
from django.conf import settings
from django.test import TestCase

from apps.discovery.post_filters import (
    is_generic_compliment_comment,
    is_irrelevant_post_for_business,
)


class TestPostFiltersGoldenFixtures(TestCase):
    def test_stone_business_real_estate_filter(self):
        stone_domain = "تولید و فروش سنگ ساختمانی و تراورتن"

        # Case 1: Real estate listing without stone sales keywords -> Must be flagged as irrelevant
        real_estate_caption = (
            "فروش فوری ویلا دوبلکس در نوشهر، ۳۰۰ متر زمین، سند تک‌برگ، استخردار، قیمت مناسب"
        )
        is_irrel, reason = is_irrelevant_post_for_business(real_estate_caption, stone_domain)
        self.assertTrue(is_irrel)
        self.assertIn("املاک", reason)

        # Case 2: Stone sales post with quarry / factory keywords -> Must NOT be flagged as irrelevant
        stone_sales_caption = (
            "تولید و عرضه مستقیم سنگ تراورتن عباس آباد سوپر، متراژ بالا، بارگیری از درب کارخانه"
        )
        is_irrel_2, reason_2 = is_irrelevant_post_for_business(stone_sales_caption, stone_domain)
        self.assertFalse(is_irrel_2)
        self.assertEqual(reason_2, "")

    def test_generic_compliment_comment_filtering(self):
        # Pure compliments & greetings -> True (no buying intent)
        compliments = [
            "خسته نباشید مهندس",
            "چقدر عالیه داداش",
            "بسیار زیبا و شیک",
            "دمتون گرم",
            "❤️❤️❤️👏👏👏",
            "وقت بخیر",
            "",
            "   ",
        ]
        for c in compliments:
            self.assertTrue(is_generic_compliment_comment(c), f"Failed for compliment: {c}")

        # Inquiries with buying intent -> False (must NOT be filtered out)
        inquiries = [
            "سلام قیمت متری چنده؟",
            "ارسال به تبریز هم دارید؟",
            "حداقل سفارش چقدره؟ کاتالوگ می‌فرستید؟",
            "سایز ۴۰ طولی موجود دارید؟",
        ]
        for q in inquiries:
            self.assertFalse(is_generic_compliment_comment(q), f"Failed for inquiry: {q}")

    def test_active_scraper_command_uses_active_filters(self):
        scraper_cmd_path = os.path.join(
            str(settings.BASE_DIR),
            "apps",
            "discovery",
            "management",
            "commands",
            "run_instagram_scraper.py",
        )
        with open(scraper_cmd_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn(
            "from apps.discovery.post_filters import is_irrelevant_post_for_business",
            content,
            "Active scraper command must import from apps.discovery.post_filters",
        )

    def test_legacy_post_filters_is_preserved_and_frozen(self):
        legacy_path = os.path.join(str(settings.BASE_DIR), "instagram_engine", "post_filters.py")
        self.assertTrue(os.path.exists(legacy_path), "instagram_engine/post_filters.py must exist")
        with open(legacy_path, "r", encoding="utf-8") as f:
            legacy_content = f.read()
        self.assertIn("def is_irrelevant_post_for_business", legacy_content)
