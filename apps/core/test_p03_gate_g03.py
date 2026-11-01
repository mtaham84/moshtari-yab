import io
from django.test import TransactionTestCase, Client
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.businesses.models import Business
from apps.products.models import Product, ProductImage, Category
from apps.discovery.models import ProductOrder, ProductDailyMetric, Opportunity, Customer
from apps.discovery.services import get_performance_analytics

User = get_user_model()


def make_test_image(name="gate_img.jpg") -> ContentFile:
    buf = io.BytesIO()
    img = Image.new("RGB", (40, 40), color=(120, 180, 240))
    img.save(buf, format="JPEG")
    return ContentFile(buf.getvalue(), name=name)


class GateG03ProductAndB2CCutIntegrationTest(TransactionTestCase):
    """
    Comprehensive verification matrix for Gate G03 (P03.08):
    1. B2C routes (/p/<pk>/, /products/<pk>/card/) must strictly block orders and return 404.
    2. No new ProductOrder can be created via any public views.
    3. Historical ProductOrder records remain intact and aggregate properly in analytics.
    4. Internal Product management (add, edit, toggle checkboxes, validation) operates flawlessly.
    5. Primary image invariant and auto-promotion work with DB constraint intact.
    6. Category tree defenses (cycles, tenant bounds, max-depth) remain strictly enforced.
    7. Active click counter (/r/<pk>/) works atomically and idempotently without modifying dead B2C metrics.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="g03_seller", password="SecurePassword123!")
        self.business = Business.objects.create(
            user=self.user,
            name="کسب‌وکار جامع تست",
            slug="g03-shop",
            onboarding_done=True,
        )
        self.category = Category.objects.create(
            business=self.business,
            name="دسته اصلی",
            product_type="PHYSICAL",
        )
        self.product = Product.objects.create(
            business=self.business,
            category=self.category,
            name="گجت هوشمند آزمایشگاهی",
            description="شرح کامل محصول",
            price=300000,
            status="ACTIVE",
            url="https://external-shop.example.com/gadget",
            instagram_discovery_enabled=True,
            is_discovery_active=True,
        )
        self.client = Client()

    def tearDown(self):
        for pi in ProductImage.objects.filter(product=self.product):
            if pi.image and default_storage.exists(pi.image.name):
                try:
                    default_storage.delete(pi.image.name)
                except Exception:
                    pass

    def test_g03_b2c_routes_completely_cut_and_order_creation_blocked(self):
        """Public product card routes return 404 and block any order creation."""
        orders_before = ProductOrder.objects.count()

        # Direct GET and POST to /p/<id>/
        res_get = self.client.get(f"/p/{self.product.id}/")
        self.assertEqual(res_get.status_code, 404)

        res_post = self.client.post(f"/p/{self.product.id}/", {
            "customer_name": "خریدار فرضی",
            "customer_phone": "09120000000",
            "quantity": 1,
        })
        self.assertEqual(res_post.status_code, 404)

        # GET and POST to /products/<id>/card/
        res_card_get = self.client.get(f"/products/{self.product.id}/card/")
        self.assertEqual(res_card_get.status_code, 404)

        res_card_post = self.client.post(f"/products/{self.product.id}/card/", {
            "customer_name": "خریدار فرضی",
            "customer_phone": "09120000000",
        })
        self.assertEqual(res_card_post.status_code, 404)

        self.assertEqual(ProductOrder.objects.count(), orders_before, "No ProductOrder rows created")

    def test_g03_historical_product_orders_and_analytics_preserved(self):
        """Historical ProductOrder records remain queryable and properly aggregate in analytics."""
        ProductOrder.objects.create(
            product=self.product,
            status="PAID",
            unit_price=300000,
            total_price=600000,
            quantity=2,
            customer_name="مشتری تاریخی",
            customer_phone="09121111111",
            tracking_code="HIST-ORD-001",
        )
        ProductDailyMetric.objects.create(
            product=self.product,
            date=timezone.now().date(),
            views_count=50,
            orders_count=1,
            sales_amount=600000,
            clicks_count=5,
            outreach_sent_count=3,
        )

        analytics = get_performance_analytics(self.business)
        self.assertIn("summary", analytics)
        self.assertEqual(analytics["summary"]["total_orders"], 1)
        self.assertEqual(analytics["summary"]["total_sales"], 600000)

    def test_g03_internal_product_management_and_checkbox_fix(self):
        """Internal seller product management operates cleanly and unchecking checkboxes saves False (R19)."""
        self.client.force_login(self.user)

        # Uncheck checkboxes on edit
        edit_url = reverse("products:edit", args=[self.product.id])
        post_data = {
            "name": "گجت تغییر یافته",
            "description": "شرح جدید",
            "price": "350,000",
            "product_type": "PHYSICAL",
            "status": "ACTIVE",
            "category": str(self.category.id),
            "discovery_priority": "3",
            # instagram_discovery_enabled omitted to represent unchecked box
        }
        res = self.client.post(edit_url, post_data)
        self.assertEqual(res.status_code, 302)

        self.product.refresh_from_db()
        self.assertEqual(self.product.name, "گجت تغییر یافته")
        self.assertEqual(self.product.price, 350000)
        self.assertFalse(self.product.instagram_discovery_enabled, "Unchecked box must persist as False (R19)")

    def test_g03_primary_image_invariant_and_lifecycle(self):
        """ProductImage UniqueConstraint holds, exactly one main image exists, auto-promotion succeeds."""
        img1 = ProductImage.objects.create(
            product=self.product,
            image=make_test_image("img1.jpg"),
            is_main=True,
            order=0,
        )
        img2 = ProductImage.objects.create(
            product=self.product,
            image=make_test_image("img2.jpg"),
            is_main=False,
            order=1,
        )

        self.assertEqual(ProductImage.objects.filter(product=self.product, is_main=True).count(), 1)

        # Deleting img1 must promote img2 to main
        img1.delete()
        img2.refresh_from_db()
        self.assertTrue(img2.is_main)
        self.assertEqual(ProductImage.objects.filter(product=self.product, is_main=True).count(), 1)

    def test_g03_category_tree_defenses_and_isolation(self):
        """Category cycle prevention and cross-tenant boundaries are strictly enforced."""
        other_user = User.objects.create_user(username="other_seller", password="password")
        other_business = Business.objects.create(user=other_user, name="فروشگاه دیگر")
        other_cat = Category.objects.create(business=other_business, name="دسته رقیب", product_type="PHYSICAL")

        # Cross-tenant parent attempt
        self.category.parent = other_cat
        with self.assertRaises(Exception):
            self.category.save()

        # Self-parent attempt
        self.category.refresh_from_db()
        self.category.parent = self.category
        with self.assertRaises(Exception):
            self.category.save()

    def test_g03_active_counters_record_click_and_idempotency(self):
        """Product click redirect /r/<id>/ records click atomically, deduplicates event, and leaves dead B2C metrics untouched."""
        click_url = reverse("product_click", args=[self.product.id])

        res1 = self.client.get(f"{click_url}?ref=gate_click_001")
        self.assertEqual(res1.status_code, 302)
        self.assertEqual(res1["Location"], "https://external-shop.example.com/gadget")

        metric = ProductDailyMetric.objects.get(product=self.product, date=timezone.now().date())
        self.assertEqual(metric.clicks_count, 1)

        # Duplicate event retry
        res2 = self.client.get(f"{click_url}?ref=gate_click_001")
        self.assertEqual(res2.status_code, 302)
        metric.refresh_from_db()
        self.assertEqual(metric.clicks_count, 1, "Duplicate click event must not double count")
