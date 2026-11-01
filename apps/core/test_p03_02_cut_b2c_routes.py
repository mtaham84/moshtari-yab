from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from apps.businesses.models import Business
from apps.products.models import Product, Category
from apps.discovery.models import ProductDailyMetric, ProductOrder

User = get_user_model()


class CutB2CRoutesTests(TestCase):
    """
    P03.02 Test Suite:
    Validates complete cut of B2C public routes and ordering callers:
    1. /p/<id>/ and /products/<id>/card/ return 404 for all GET and POST requests.
    2. Zero ProductOrder records can be created via public web requests.
    3. Inactive/active product state cannot be bypassed (both 404).
    4. product_click_view redirects to seller's external website or B2B shop (never /p/).
    5. Internal seller management and discovery views remain intact.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="b2c_seller", email="seller@test.ir", password="Password123!")
        self.business = Business.objects.create(user=self.user, name="B2B Shop", slug="b2b-shop", onboarding_done=True)
        self.category = Category.objects.create(name="الکترونیک", business=self.business)
        self.product = Product.objects.create(
            business=self.business,
            name="اسیلوسکوپ دیجیتال",
            category=self.category,
            price=15000000,
            url="https://external-store.ir/p/oscilloscope-200",
            status="ACTIVE",
        )
        self.inactive_product = Product.objects.create(
            business=self.business,
            name="کالای غیرفعال",
            category=self.category,
            price=500000,
            status="DRAFT",
        )

    def test_b2c_card_routes_return_404_on_get(self):
        """Direct GET on short and standard card routes returns 404."""
        # /p/<pk>/
        r_short = self.client.get(f"/p/{self.product.pk}/")
        self.assertEqual(r_short.status_code, 404)

        # /products/<pk>/card/
        r_card = self.client.get(f"/products/{self.product.pk}/card/")
        self.assertEqual(r_card.status_code, 404)

    def test_b2c_card_routes_return_404_on_post_and_create_no_orders(self):
        """Direct POST on card routes returns 404 and creates zero orders."""
        order_data = {"customer_name": "احمد", "customer_phone": "09121234567", "quantity": 1}
        r_post = self.client.post(f"/p/{self.product.pk}/", order_data)
        self.assertEqual(r_post.status_code, 404)
        self.assertEqual(ProductOrder.objects.count(), 0)

        r_card_post = self.client.post(f"/products/{self.product.pk}/card/", order_data)
        self.assertEqual(r_card_post.status_code, 404)
        self.assertEqual(ProductOrder.objects.count(), 0)

    def test_inactive_products_cannot_bypass_via_b2c(self):
        """Inactive products also return 404 (R22)."""
        r_inact = self.client.get(f"/p/{self.inactive_product.pk}/")
        self.assertEqual(r_inact.status_code, 404)

    def test_product_click_redirects_to_external_url_when_available(self):
        """/r/<pk>/ redirects directly to external URL and tracks click."""
        initial_clicks = ProductDailyMetric.objects.filter(product=self.product).values_list("clicks_count", flat=True).first() or 0

        resp = self.client.get(f"/r/{self.product.pk}/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "https://external-store.ir/p/oscilloscope-200")

        # Click count incremented
        updated_clicks = ProductDailyMetric.objects.get(product=self.product).clicks_count
        self.assertEqual(updated_clicks, initial_clicks + 1)

    def test_product_click_fallback_redirects_to_seller_shop_instead_of_p(self):
        """When product has no external URL, /r/<pk>/ redirects to seller shop, not dead /p/<pk>/."""
        self.product.url = ""
        self.product.save(update_fields=["url"])

        resp = self.client.get(f"/r/{self.product.pk}/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, f"/shop/{self.business.slug}/")
        self.assertNotIn("/p/", resp.url)

    def test_internal_management_remains_fully_functional(self):
        """Internal seller catalog and product details remain 100% operational."""
        self.client.force_login(self.user)
        r_detail = self.client.get(f"/products/{self.product.pk}/")
        self.assertEqual(r_detail.status_code, 200)
        # B2C CTA button was removed from template
        self.assertNotContains(r_detail, "مشاهده کارت عمومی محصول (دید مشتری)")
