from pathlib import Path
from django.conf import settings
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from apps.businesses.models import Business
from apps.products.models import Product, Category
from apps.discovery.models import ProductDailyMetric, ProductOrder
from apps.discovery.services import get_performance_analytics

User = get_user_model()


class RemoveB2CAssetsAndMetricsTests(TestCase):
    """
    P03.03 Test Suite:
    Validates complete elimination of B2C-only assets, templates, and metric sinks:
    1. public_product_card.html is deleted from template directory.
    2. Zero new B2C orders or conversions can be initiated.
    3. Historical metrics (sales_amount, orders_count) remain intact and correctly aggregate.
    4. Storefront (shop_view) renders without dead B2C card links.
    5. Product detail view has zero B2C CTA buttons.
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="b2c_asset_seller", email="seller_asset@test.ir", password="Password123!")
        self.business = Business.objects.create(user=self.user, name="Modern Tools", slug="modern-tools", onboarding_done=True)
        self.category = Category.objects.create(name="ابزار برقی", business=self.business)
        self.product = Product.objects.create(
            business=self.business,
            name="دریل شارژی",
            category=self.category,
            price=3200000,
            url="https://external-store.ir/p/drill-20",
            status="ACTIVE",
        )

    def test_public_product_card_template_is_removed(self):
        """The public_product_card.html template file must no longer exist on disk."""
        template_path = Path(settings.BASE_DIR) / "templates" / "products" / "public_product_card.html"
        self.assertFalse(template_path.exists())

    def test_b2c_order_sink_is_completely_dead(self):
        """
        Public order sink cannot be accessed or triggered.
        ProductOrder count remains zero and no new orders can be created via web.
        """
        initial_orders = ProductOrder.objects.count()
        resp = self.client.post(
            f"/p/{self.product.pk}/",
            {"customer_name": "سودجو", "customer_phone": "09129999999", "quantity": 1},
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(ProductOrder.objects.count(), initial_orders)

    def test_historical_metrics_remain_intact_and_aggregatable(self):
        """
        Existing historical metrics (sales_amount, orders_count) remain intact and
        continue to aggregate in analytics service without raising errors.
        """
        ProductDailyMetric.objects.create(
            product=self.product,
            date="2025-01-01",
            clicks_count=10,
            views_count=50,
            orders_count=3,
            sales_amount=9600000,
        )

        analytics = get_performance_analytics(self.business)
        summary = analytics["summary"]
        self.assertEqual(summary["total_orders"], 3)
        self.assertEqual(summary["total_sales"], 9600000)
        self.assertEqual(summary["total_clicks"], 10)

    def test_seller_shop_renders_without_b2c_card_links(self):
        """Storefront links directly to product.url (external) and contains no dead /card/ URLs."""
        resp = self.client.get(reverse("accounts:shop", args=[self.business.slug]))
        self.assertEqual(resp.status_code, 200)
        # Should link to external url
        self.assertContains(resp, "https://external-store.ir/p/drill-20")
        # Must not contain link to public_card
        self.assertNotContains(resp, f"/products/{self.product.pk}/card/")
        self.assertNotContains(resp, f"/p/{self.product.pk}/")

    def test_product_detail_has_no_b2c_cta(self):
        """Product detail page has no button to view public card."""
        self.client.force_login(self.user)
        resp = self.client.get(reverse("products:detail", args=[self.product.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "مشاهده کارت عمومی محصول (دید مشتری)")
