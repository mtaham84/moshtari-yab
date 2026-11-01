from decimal import Decimal
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.test import Client, TestCase
from django.urls import reverse

from apps.businesses.models import Business
from apps.products.models import Category, Product

User = get_user_model()


class P0820CategoryAttributesComponentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="seller_p0820", email="p0820@example.com", password="pass"
        )
        self.business = Business.objects.create(
            user=self.user, name="فروشگاه قطعات ۲۰", onboarding_done=True
        )
        self.category = Category.objects.create(
            name="ابزار برقی",
            business=self.business,
            suggested_attributes=[{"key": "توان", "label": "توان موتور", "placeholder": "مثال: ۱۲۰۰ وات"}],
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_component_renders_preset_chips_and_container(self):
        html = render_to_string(
            "products/components/_category_attributes.html",
            {"product": None},
        )
        self.assertIn("id=\"attributes-wrapper\"", html)
        self.assertIn("id=\"preset-chips-container\"", html)
        self.assertIn("id=\"custom-attributes-list\"", html)
        self.assertIn("data-key=\"سایز\"", html)
        self.assertIn("data-key=\"رنگ\"", html)
        self.assertIn("data-key=\"جنس\"", html)
        self.assertIn("data-key=\"برند\"", html)

    def test_component_renders_existing_attributes_with_quotes_safely(self):
        prod = Product.objects.create(
            business=self.business,
            name="دریل چکشی",
            description="دریل چکشی ۱۲۰۰ وات صنعتی",
            attributes={
                "برند": 'رونیکس "Ronix"',
                "سایز سه‌نظام": "۱۳ میلی‌متر 'استاندارد'",
                "گارانتی": "۱۲ ماهه شرکتی <معتبر>",
            },
        )
        html = render_to_string(
            "products/components/_category_attributes.html",
            {"product": prod},
        )
        # Verify rendered inputs keep Persian text and quotes intact
        self.assertIn('name="custom_attr_key"', html)
        self.assertIn('name="custom_attr_value"', html)
        self.assertIn('value="برند"', html)
        self.assertIn('value="سایز سه‌نظام"', html)
        # HTML escaping for attribute values must not break DOM
        self.assertIn('&quot;Ronix&quot;', html)
        self.assertIn('&#x27;استاندارد&#x27;', html)
        self.assertIn('&lt;معتبر&gt;', html)

    def test_post_custom_attributes_saves_cleanly(self):
        add_url = reverse("products:add")
        payload = {
            "name": "فرز نجاری",
            "description": "فرز اورجینال نجاری با لوازم جانبی",
            "product_type": "PHYSICAL",
            "status": "ACTIVE",
            "price": "4500000",
            "category_id": str(self.category.id),
            "custom_attr_key": ["تیغه‌ها", "توان", "کشور سازنده"],
            "custom_attr_value": ['سایز "۶ و ۸"', "۱۴۰۰ وات", "تایوان 'درجه یک'"],
        }
        res = self.client.post(add_url, payload)
        self.assertEqual(res.status_code, 302)

        created = Product.objects.get(name="فرز نجاری")
        self.assertEqual(created.attributes.get("تیغه‌ها"), 'سایز "۶ و ۸"')
        self.assertEqual(created.attributes.get("توان"), "۱۴۰۰ وات")
        self.assertEqual(created.attributes.get("کشور سازنده"), "تایوان 'درجه یک'")

    def test_edit_custom_attributes_updates_cleanly(self):
        prod = Product.objects.create(
            business=self.business,
            name="سمباده لرزان",
            description="سمباده لرزان نجاری",
            attributes={"سرعت": "۱۰۰۰ دور"},
        )
        edit_url = reverse("products:edit", args=[prod.pk])
        payload = {
            "name": "سمباده لرزان به‌روزشده",
            "description": "سمباده لرزان نجاری نسخه جدید",
            "product_type": "PHYSICAL",
            "status": "ACTIVE",
            "price": "1800000",
            "custom_attr_key": ["سرعت", "رنگ"],
            "custom_attr_value": ["۱۲۰۰ دور در دقیقه", "آبی کاربنی"],
        }
        res = self.client.post(edit_url, payload)
        self.assertEqual(res.status_code, 302)

        prod.refresh_from_db()
        self.assertEqual(prod.attributes.get("سرعت"), "۱۲۰۰ دور در دقیقه")
        self.assertEqual(prod.attributes.get("رنگ"), "آبی کاربنی")
