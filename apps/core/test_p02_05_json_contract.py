import json
import re
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from bs4 import BeautifulSoup

from apps.businesses.models import Business
from apps.products.models import Category, Product

User = get_user_model()


class FormJsonContractTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="seller_form_test", password="Password123!")
        self.business = Business.objects.create(name="فروشگاه فرم تست", user=self.user, onboarding_done=True)
        self.cat1 = Category.objects.create(
            business=self.business,
            name="پوشاک زنانه",
            suggested_attributes=[
                {"key": "material", "label": "جنس پارچه", "placeholder": "مثلاً نخ و پنبه"},
                {"key": "size", "label": "سایز", "placeholder": "مثلاً 38 تا 44"},
            ],
        )
        self.cat_malicious = Category.objects.create(
            business=self.business,
            name="دسته تست پی‌لود",
            suggested_attributes=[
                {
                    "key": "xss",
                    "label": "ویژگی \"نقل‌قول‌دار\" & فارسی </script><script>alert('xss')</script>",
                    "placeholder": "مقدار </script>",
                }
            ],
        )
        self.product = Product.objects.create(
            business=self.business,
            category=self.cat1,
            name="مانتو تابستانه",
            description="مانتو خنک مناسب فصل",
            price=350000,
        )

    def test_product_form_renders_independent_json_script_tag(self):
        """
        product_form must output category-attributes-data as a safe top-level script tag,
        and must NOT embed json_script output inside data-attrs attributes.
        """
        self.client.force_login(self.user)
        res = self.client.get(reverse("products:add"))
        self.assertEqual(res.status_code, 200)

        html = res.content.decode("utf-8")

        # 1. Must NOT have data-attrs with <script> tag inside option
        self.assertNotIn("data-attrs='<script", html)
        self.assertNotIn('data-attrs="<script', html)
        self.assertNotIn("suggested_attributes|json_script|safe", html)

        # 2. Must contain independent <script id="category-attributes-data" type="application/json">
        soup = BeautifulSoup(html, "html.parser")
        script_tag = soup.find("script", id="category-attributes-data")
        self.assertIsNotNone(script_tag, "Missing #category-attributes-data script element in HTML")
        self.assertEqual(script_tag.get("type"), "application/json")

        # 3. Parse JSON from script tag
        data = json.loads(script_tag.string)
        self.assertIn(str(self.cat1.id), data)
        self.assertEqual(len(data[str(self.cat1.id)]), 2)
        self.assertEqual(data[str(self.cat1.id)][0]["key"], "material")

    def test_quotes_persian_and_closing_script_tags_do_not_break_dom(self):
        """
        Quotes, Persian characters, and </script> tags in category attributes must be safely
        escaped by json_script and must not break the DOM or spawn rogue script tags.
        """
        self.client.force_login(self.user)
        res = self.client.get(reverse("products:add"))
        self.assertEqual(res.status_code, 200)

        html = res.content.decode("utf-8")
        soup = BeautifulSoup(html, "html.parser")
        script_tag = soup.find("script", id="category-attributes-data")
        self.assertIsNotNone(script_tag)

        # Verify parsed data contains raw text safely preserved
        data = json.loads(script_tag.string)
        malicious_attrs = data[str(self.cat_malicious.id)]
        self.assertIn("</script><script>alert('xss')</script>", malicious_attrs[0]["label"])

        # Verify raw HTML does NOT contain an unescaped literal </script> inside the json string
        # (Django json_script escapes < and > as \u003C and \u003E)
        raw_script_content = script_tag.string
        self.assertNotIn("</script><script>", raw_script_content)
        self.assertIn("\\u003C/script\\u003E", raw_script_content)

    def test_product_edit_renders_independent_json_script_tag(self):
        """
        product_edit_view also renders the independent category-attributes-data script tag.
        """
        self.client.force_login(self.user)
        res = self.client.get(reverse("products:edit", args=[self.product.pk]))
        self.assertEqual(res.status_code, 200)

        soup = BeautifulSoup(res.content.decode("utf-8"), "html.parser")
        script_tag = soup.find("script", id="category-attributes-data")
        self.assertIsNotNone(script_tag)

        data = json.loads(script_tag.string)
        self.assertIn(str(self.cat1.id), data)

    def test_form_submission_creates_product_with_attributes_successfully(self):
        """
        Standard form submission continues to work with existing input names and attributes.
        """
        self.client.force_login(self.user)
        post_data = {
            "name": "محصول تست قرارداد فرم",
            "description": "توضیحات تست قرارداد فرم",
            "price": "420000",
            "product_type": "PHYSICAL",
            "category_id": str(self.cat1.id),
            "custom_attr_key": ["جنس", "رنگ"],
            "custom_attr_value": ["نخ", "سرمه‌ای"],
        }
        res = self.client.post(reverse("products:add"), post_data)
        self.assertEqual(res.status_code, 302)

        created = Product.objects.filter(name="محصول تست قرارداد فرم").first()
        self.assertIsNotNone(created)
        self.assertEqual(created.category_id, self.cat1.id)
        self.assertEqual(created.attributes.get("جنس"), "نخ")
        self.assertEqual(created.attributes.get("رنگ"), "سرمه‌ای")
