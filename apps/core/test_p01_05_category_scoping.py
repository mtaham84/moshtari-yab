from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from apps.businesses.models import Business
from apps.products.models import Category
from apps.products.services import suggest_category_for_product
from apps.products.views import _categories_for

User = get_user_model()


class CategoryScopingTestCase(TestCase):
    """
    P01.05 Test Suite:
    Validates complete elimination of global private category fallbacks (R13):
    - Tenant A without own categories and without system categories sees NOTHING from Tenant B.
    - System categories are accessible to all authenticated sellers.
    - Private category attributes are strictly forbidden to non-owners (404).
    - Category helper selectors never leak foreign tenant categories.
    """

    def setUp(self):
        self.client = Client()

        # Tenant A
        self.user_a = User.objects.create_user(username="seller_a@example.com", password="password123")
        self.business_a = Business.objects.create(user=self.user_a, name="فروشگاه الف", onboarding_done=True)

        # Tenant B
        self.user_b = User.objects.create_user(username="seller_b@example.com", password="password123")
        self.business_b = Business.objects.create(user=self.user_b, name="فروشگاه ب", onboarding_done=True)

        # Superuser
        self.admin = User.objects.create_superuser(username="admin@example.com", password="password123")

        # Deactivate any pre-existing fixtures/system categories for isolated boundary tests
        Category.objects.all().delete()

        # System Category (Public / Shared)
        self.system_cat = Category.objects.create(
            name="پوشاک و مد عمومی",
            business=None,
            is_active=True,
            suggested_attributes=["جنس پارچه", "رنگ", "سایز"],
        )

        # Business B's Private Category
        self.private_cat_b = Category.objects.create(
            name="شال و روسری ابریشم اختصاصی ب",
            business=self.business_b,
            is_active=True,
            suggested_attributes=["طراح اختصاصی ب", "درجه ابریشم"],
        )

        # Business A's Private Category
        self.private_cat_a = Category.objects.create(
            name="کیف اداری چرم اختصاصی الف",
            business=self.business_a,
            is_active=True,
            suggested_attributes=["تعداد جیب", "نوع قفل الف"],
        )

    def test_tenant_a_without_categories_and_no_system_categories_sees_nothing_from_tenant_b(self):
        """
        Criterion 1: When Tenant A has no matching categories and system categories are absent,
        Tenant A MUST NOT match or see Tenant B's private categories.
        """
        # Deactivate system category and Tenant A's category
        self.system_cat.is_active = False
        self.system_cat.save()
        self.private_cat_a.is_active = False
        self.private_cat_a.save()

        # Query matching Tenant B's private category name
        suggestion = suggest_category_for_product(
            name="شال و روسری ابریشم اختصاصی",
            business=self.business_a,
        )
        self.assertIsNone(suggestion)

        # Also verify via HTTP API endpoint
        self.client.force_login(self.user_a)
        url = reverse("products:api_suggest_category")
        res = self.client.get(url, {"name": "شال و روسری ابریشم اختصاصی"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("status"), "no_match")
        self.assertIsNone(res.json().get("suggestion"))

    def test_system_category_allowed_and_accessible_to_any_tenant(self):
        """
        Criterion 2a: System categories (business=None) are legitimate shared references
        and can be queried by any authenticated tenant.
        """
        self.client.force_login(self.user_a)
        url = reverse("products:api_category_attributes", kwargs={"category_id": self.system_cat.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json().get("attributes"), ["جنس پارچه", "رنگ", "سایز"])

        self.client.force_login(self.user_b)
        res_b = self.client.get(url)
        self.assertEqual(res_b.status_code, 200)
        self.assertEqual(res_b.json().get("attributes"), ["جنس پارچه", "رنگ", "سایز"])

    def test_private_category_attributes_strictly_forbidden_to_non_owner(self):
        """
        Criterion 2b: Tenant A cannot inspect Business B's private category attributes.
        Returns 404 fail-closed.
        """
        self.client.force_login(self.user_a)
        url = reverse("products:api_category_attributes", kwargs={"category_id": self.private_cat_b.id})
        res = self.client.get(url)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json().get("attributes"), [])

    def test_private_category_attributes_accessible_to_owner_and_superuser(self):
        """
        Owner (Tenant B) and Superuser can access Business B's category attributes.
        """
        url = reverse("products:api_category_attributes", kwargs={"category_id": self.private_cat_b.id})

        # Owner
        self.client.force_login(self.user_b)
        res_b = self.client.get(url)
        self.assertEqual(res_b.status_code, 200)
        self.assertEqual(res_b.json().get("attributes"), ["طراح اختصاصی ب", "درجه ابریشم"])

        # Superuser
        self.client.force_login(self.admin)
        res_admin = self.client.get(url)
        self.assertEqual(res_admin.status_code, 200)
        self.assertEqual(res_admin.json().get("attributes"), ["طراح اختصاصی ب", "درجه ابریشم"])

    def test_categories_for_selector_never_leaks_foreign_tenant_categories(self):
        """
        _categories_for helper function strictly confines results to business own + system categories.
        """
        cats_for_a = list(_categories_for(self.business_a))
        cat_ids_for_a = {c.id for c in cats_for_a}

        self.assertIn(self.private_cat_a.id, cat_ids_for_a)
        self.assertNotIn(self.private_cat_b.id, cat_ids_for_a)
