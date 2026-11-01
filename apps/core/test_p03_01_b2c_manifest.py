from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from apps.businesses.models import Business
from apps.products.models import Product, Category
from apps.discovery.models import Opportunity, Customer, ProductOrder

User = get_user_model()


class B2CDependencyManifestTests(TestCase):
    """
    P03.01 Test Suite:
    Validates B2C Removal Manifest & Protected Boundaries:
    1. B2C Endpoints to be eliminated:
       - 'short_public_card' (/p/<id>/)
       - 'products:public_card' (/products/<id>/card/)
       - Public order creation sink in public_product_card_view
    2. Protected Internal Product & B2B Surfaces (Must remain fully functional):
       - Internal product catalog: 'products:list', 'products:detail', 'products:add', 'products:edit'
       - B2B Showcase: 'accounts:shop' (/shop/<slug>/)
       - Discovery opportunities & lead generation engine
       - Historical ProductOrder data preserved in database schema
    """

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="b2c_manifest_seller", email="seller@b2c.com", password="Password123!")
        self.business = Business.objects.create(user=self.user, name="ManifestStore", slug="manifest-store", onboarding_done=True)
        self.category = Category.objects.create(name="ابزار دقیق", business=self.business)
        self.product = Product.objects.create(
            business=self.business,
            name="مولتی‌متر دیجیتال",
            category=self.category,
            price=2500000,
            url="https://sellerstore.ir/p/multimeter-101",
            status="ACTIVE",
        )
        self.customer = Customer.objects.create(
            business=self.business,
            source_platform="instagram",
            external_user_id="cust_123",
            source_username="buyer_account",
        )
        self.opportunity = Opportunity.objects.create(
            business=self.business,
            customer=self.customer,
            source_platform="instagram",
            status="NEW",
        )

    def test_b2c_identified_routes_and_order_sink(self):
        """
        Confirms B2C public endpoints exist in current codebase and order creation
        is properly identified in the removal manifest.
        """
        # Identified B2C route 1: short_public_card (/p/<pk>/)
        url_short = reverse("short_public_card", args=[self.product.pk])
        self.assertEqual(url_short, f"/p/{self.product.pk}/")

        # Identified B2C route 2: public_card (/products/<pk>/card/)
        url_card = reverse("products:public_card", args=[self.product.pk])
        self.assertEqual(url_card, f"/products/{self.product.pk}/card/")

        # Containment gate & B2C cut block POST order (R06, P03.02)
        resp = self.client.post(url_short, {"customer_name": "Test", "customer_phone": "09121111111", "quantity": 1})
        self.assertIn(resp.status_code, [403, 404])

    def test_internal_product_and_discovery_pages_are_protected(self):
        """
        Confirms that internal seller management pages and discovery flows
        are strictly protected and isolated from the B2C surface.
        """
        self.client.force_login(self.user)

        # Product management
        r_list = self.client.get(reverse("products:list"))
        self.assertEqual(r_list.status_code, 200)

        r_detail = self.client.get(reverse("products:detail", args=[self.product.pk]))
        self.assertEqual(r_detail.status_code, 200)

        # Discovery opportunities
        r_opps = self.client.get(reverse("discovery:opportunity_list"))
        self.assertEqual(r_opps.status_code, 200)

    def test_historical_product_order_data_is_preserved_and_protected(self):
        """
        Existing historical ProductOrder records must remain intact for audit/reporting;
        table must not be dropped.
        """
        hist_order = ProductOrder.objects.create(
            product=self.product,
            opportunity=self.opportunity,
            customer_name="خریدار تاریخی",
            customer_phone="09123456789",
            quantity=2,
            total_price=5000000,
            status="PAID",
        )
        self.assertTrue(ProductOrder.objects.filter(pk=hist_order.pk).exists())
        loaded = ProductOrder.objects.get(pk=hist_order.pk)
        self.assertEqual(loaded.status, "PAID")
        self.assertEqual(loaded.customer_name, "خریدار تاریخی")

    def test_b2b_shop_showcase_is_protected(self):
        """
        Public storefront /shop/<slug>/ is a merchant profile showcase and remains protected,
        independent of consumer ordering.
        """
        resp = self.client.get(reverse("accounts:shop", args=[self.business.slug]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ManifestStore")
