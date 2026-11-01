from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase

from apps.businesses.models import Business
from apps.products import services
from apps.products.models import Category, Product, ProductImage

User = get_user_model()


class P0817ThinProductViewsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="seller_p0817", email="p0817@example.com", password="pass")
        self.business = Business.objects.create(user=self.user, name="فروشگاه آزمایشی ۸۱۷")
        self.category = Category.objects.create(name="لوازم خانگی", business=self.business)

    def test_categories_for_business_scoping(self):
        cats = services.categories_for_business(self.business)
        self.assertIn(self.category, list(cats))

    def test_parse_and_validate_price_variants(self):
        # Empty or None -> (None, None)
        self.assertEqual(services.parse_and_validate_price(""), (None, None))
        self.assertEqual(services.parse_and_validate_price(None), (None, None))

        # Persian and English commas
        val, err = services.parse_and_validate_price("۱،۵۰۰،۰۰۰")
        self.assertEqual(val, Decimal("1500000"))
        self.assertIsNone(err)

        val_en, err_en = services.parse_and_validate_price(" 2,500,000 ")
        self.assertEqual(val_en, Decimal("2500000"))
        self.assertIsNone(err_en)

        # Negative price rejected
        val_neg, err_neg = services.parse_and_validate_price("-1000")
        self.assertIsNone(val_neg)
        self.assertIn("منفی", err_neg)

        # Invalid format rejected
        val_bad, err_bad = services.parse_and_validate_price("bad_number")
        self.assertIsNone(val_bad)
        self.assertIn("نامعتبر", err_bad)

    def test_validate_product_input_and_checkbox_handling(self):
        cats_qs = Category.objects.filter(id=self.category.id)

        # Missing name and description
        cleaned, errors = services.validate_product_input({}, cats_qs)
        self.assertIn("نام محصول یا خدمت الزامی است.", errors)
        self.assertIn("شرح مزایا و کاربرد محصول برای شناخت ایجنت الزامی است.", errors)

        # Valid payload with unchecked checkboxes (must resolve to False)
        valid_payload = {
            "name": "جاروبرقی هوشمند",
            "description": "جاروبرقی رباتیک با قدرت مکش بالا",
            "product_type": "PHYSICAL",
            "status": "ACTIVE",
            "discovery_priority": "3",
            "price": "12000000",
            "category_id": str(self.category.id),
        }
        cleaned_valid, errors_valid = services.validate_product_input(valid_payload, cats_qs)
        self.assertEqual(len(errors_valid), 0)
        self.assertEqual(cleaned_valid["name"], "جاروبرقی هوشمند")
        self.assertEqual(cleaned_valid["price"], Decimal("12000000"))
        self.assertEqual(cleaned_valid["category"], self.category)
        self.assertFalse(cleaned_valid["instagram_discovery_enabled"])
        self.assertFalse(cleaned_valid["is_discovery_active"])

    def test_parse_dynamic_attributes(self):
        post_data = {
            "custom_attr_key": ["رنگ", "گارانتی"],
            "custom_attr_value": ["مشکی", "۱۸ ماهه شرکتی"],
            "attr_brand": "سامسونگ",
        }
        req = self.factory.post("/", post_data)
        attrs = services.parse_dynamic_attributes(req.POST)
        self.assertEqual(attrs.get("رنگ"), "مشکی")
        self.assertEqual(attrs.get("گارانتی"), "۱۸ ماهه شرکتی")
        self.assertEqual(attrs.get("brand"), "سامسونگ")

    def test_save_product_lifecycle_create_and_update(self):
        cleaned_data = {
            "name": "میکسر آشپزخانه",
            "description": "میکسر قدرتمند خانگی",
            "product_type": "PHYSICAL",
            "status": "ACTIVE",
            "discovery_priority": 1,
            "price": Decimal("3500000"),
            "category": self.category,
            "url": "https://example.com/mixer",
            "target_customer": "خانه‌داران",
            "instagram_discovery_enabled": True,
            "is_discovery_active": True,
            "telegram_outreach_enabled": False,
            "x_outreach_enabled": False,
            "x_search_enabled": False,
            "instagram_terms": ["میکسر"],
            "instagram_hashtags": ["میکسر_خانگی"],
            "instagram_max_posts": 5,
            "instagram_window_days": 7,
        }
        attrs = {"توان": "۸۰۰ وات"}

        # 1. Create product
        prod, organizing = services.save_product(
            self.business,
            cleaned_data,
            attrs,
            valid_images=[],
        )
        self.assertIsNotNone(prod.pk)
        self.assertEqual(prod.name, "میکسر آشپزخانه")
        self.assertEqual(prod.attributes["توان"], "۸۰۰ وات")

        # 2. Update product
        cleaned_data["name"] = "میکسر آشپزخانه پیشرفته"
        attrs["رنگ"] = "نقره‌ای"
        updated_prod, _ = services.save_product(
            self.business,
            cleaned_data,
            attrs,
            valid_images=[],
            product=prod,
        )
        self.assertEqual(updated_prod.pk, prod.pk)
        self.assertEqual(updated_prod.name, "میکسر آشپزخانه پیشرفته")
        self.assertEqual(updated_prod.attributes["رنگ"], "نقره‌ای")

    def test_resolve_product_save_error(self):
        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.db import IntegrityError

        val_msg = services.resolve_product_save_error(DjangoValidationError("خطای اعتبارسنجی"))
        self.assertIn("نامعتبر است", val_msg)

        integ_msg = services.resolve_product_save_error(IntegrityError("unique constraint"))
        self.assertIn("تکراری یا تداخل", integ_msg)

        generic_msg = services.resolve_product_save_error(RuntimeError("crash"))
        self.assertIn("خطا در پردازش", generic_msg)
