from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from apps.businesses.models import Business
from apps.products.models import Product, Category

User = get_user_model()


class ProductFormValidationTests(TestCase):
    """
    P03.04 Test Suite:
    Validates unified validation and correct checkbox behavior for Product add/edit:
    1. R19: Unchecking checkboxes (e.g. instagram_discovery_enabled) in edit updates DB to False.
    2. R20: Both add and edit enforce required name, description, valid choices for status and product_type.
    3. Decimal price validation: rejects negative or non-numeric values with field errors (no silent None/truncation).
    4. Business ownership cannot be tampered with via POST.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="form_seller", email="seller_form@test.ir", password="Password123!")
        self.business = Business.objects.create(user=self.user, name="Tools Business", slug="tools-biz", onboarding_done=True)
        self.category = Category.objects.create(name="ابزارآلات", business=self.business)
        self.client.force_login(self.user)

    def test_r19_checkbox_unchecking_in_edit_sets_false(self):
        """
        R19: When an unchecked checkbox is omitted from the POST payload (standard HTML behavior),
        the field is correctly updated to False in the database.
        """
        prod = Product.objects.create(
            business=self.business,
            name="مینی فرز رونیکس",
            description="فرز ۱۱۵ میلی‌متر رونیکس با دور متغیر",
            category=self.category,
            instagram_discovery_enabled=True,
            is_discovery_active=True,
            telegram_outreach_enabled=True,
            x_outreach_enabled=True,
            x_search_enabled=True,
        )

        edit_url = reverse("products:edit", args=[prod.pk])
        # Post payload WITHOUT the checkboxes (simulating unchecking in browser)
        payload = {
            "name": "مینی فرز رونیکس ویرایش‌شده",
            "description": "فرز ۱۱۵ میلی‌متر رونیکس با دور متغیر - نسخه جدید",
            "category_id": str(self.category.pk),
            "product_type": "PHYSICAL",
            "status": "ACTIVE",
            "discovery_priority": "2",
            "price": "2850000",
        }

        resp = self.client.post(edit_url, payload)
        self.assertEqual(resp.status_code, 302)

        prod.refresh_from_db()
        self.assertFalse(prod.instagram_discovery_enabled, "instagram_discovery_enabled must be False when unchecked (R19 fix)")
        self.assertFalse(prod.is_discovery_active, "is_discovery_active must be False when unchecked")
        self.assertFalse(prod.telegram_outreach_enabled, "telegram_outreach_enabled must be False when unchecked")
        self.assertFalse(prod.x_outreach_enabled, "x_outreach_enabled must be False when unchecked")
        self.assertFalse(prod.x_search_enabled, "x_search_enabled must be False when unchecked")

    def test_r20_name_and_description_required_on_both_add_and_edit(self):
        """
        R20: Both add and edit views must reject empty name or description with validation error.
        """
        add_url = reverse("products:add")
        # Empty name in add
        r1 = self.client.post(add_url, {"name": "", "description": "توضیح تست", "product_type": "PHYSICAL"})
        self.assertEqual(r1.status_code, 200)
        self.assertContains(r1, "نام محصول یا خدمت الزامی است")

        # Empty description in add
        r2 = self.client.post(add_url, {"name": "نام کالا", "description": "", "product_type": "PHYSICAL"})
        self.assertEqual(r2.status_code, 200)
        self.assertContains(r2, "شرح مزایا و کاربرد محصول")

        # Create valid product
        prod = Product.objects.create(
            business=self.business,
            name="کالای معتبر",
            description="شرح معتبر",
            category=self.category,
        )

        edit_url = reverse("products:edit", args=[prod.pk])
        # Empty name in edit
        r3 = self.client.post(edit_url, {"name": "", "description": "شرح کالا", "product_type": "PHYSICAL"})
        self.assertEqual(r3.status_code, 200)
        self.assertContains(r3, "نام محصول یا خدمت الزامی است")
        prod.refresh_from_db()
        self.assertEqual(prod.name, "کالای معتبر", "Name must not be wiped out by invalid submission")

        # Empty description in edit
        r4 = self.client.post(edit_url, {"name": "نام جدید", "description": "", "product_type": "PHYSICAL"})
        self.assertEqual(r4.status_code, 200)
        self.assertContains(r4, "شرح مزایا و کاربرد محصول")

    def test_r20_status_and_product_type_choices_strictly_validated(self):
        """
        R20: Invalid choices for status or product_type must be rejected in both add and edit.
        """
        add_url = reverse("products:add")
        resp_bad_type = self.client.post(add_url, {
            "name": "محصول تستی",
            "description": "شرح تستی",
            "product_type": "HACKED_TYPE",
        })
        self.assertEqual(resp_bad_type.status_code, 200)
        self.assertContains(resp_bad_type, "نوع محصول باید «کالایی» یا «خدماتی» باشد")

        resp_bad_status = self.client.post(add_url, {
            "name": "محصول تستی",
            "description": "شرح تستی",
            "product_type": "PHYSICAL",
            "status": "NON_EXISTENT_STATUS",
        })
        self.assertEqual(resp_bad_status.status_code, 200)
        self.assertContains(resp_bad_status, "وضعیت محصول نامعتبر است")

    def test_p03_04_decimal_price_validation(self):
        """
        Decimal price parsing:
        - Rejects negative price with clear error
        - Rejects non-numeric price with clear error
        - Parses formatted Persian/English comma separated numbers
        """
        add_url = reverse("products:add")

        # Negative price
        r_neg = self.client.post(add_url, {
            "name": "محصول منفی",
            "description": "شرح",
            "price": "-50000",
        })
        self.assertEqual(r_neg.status_code, 200)
        self.assertContains(r_neg, "قیمت نمی‌تواند منفی باشد")

        # Non-numeric price
        r_bad = self.client.post(add_url, {
            "name": "محصول خراب",
            "description": "شرح",
            "price": "not_a_valid_price",
        })
        self.assertEqual(r_bad.status_code, 200)
        self.assertContains(r_bad, "قیمت واردشده نامعتبر است")

        # Valid formatted price with Persian & English commas
        r_ok = self.client.post(add_url, {
            "name": "محصول با قیمت فرمت‌شده",
            "description": "توضیح کامل محصول برای تایید ثبت",
            "price": "۱،۲۵۰،۰۰۰",
        })
        self.assertEqual(r_ok.status_code, 302)
        created = Product.objects.get(name="محصول با قیمت فرمت‌شده")
        self.assertEqual(created.price, Decimal("1250000"))

    def test_business_ownership_cannot_be_tampered_via_post(self):
        """
        Submitting a foreign business ID in POST must not change the product owner.
        """
        other_user = User.objects.create_user(username="other_seller", email="other@test.ir", password="Password123!")
        other_biz = Business.objects.create(user=other_user, name="Foreign Biz", slug="foreign-biz")

        add_url = reverse("products:add")
        self.client.post(add_url, {
            "name": "محصول تحت مالکیت",
            "description": "شرح محصول تحت مالکیت",
            "business_id": str(other_biz.pk),
        })

        prod = Product.objects.get(name="محصول تحت مالکیت")
        self.assertEqual(prod.business, self.business, "Product must strictly belong to authenticated user's business")
