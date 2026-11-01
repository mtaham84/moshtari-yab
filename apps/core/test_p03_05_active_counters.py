import threading
from django.test import TransactionTestCase, Client
from django.db import connection
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from apps.businesses.models import Business
from apps.products.models import Product
from apps.discovery.models import ProductDailyMetric

User = get_user_model()


class ActiveProductCountersTestCase(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="metric_owner", password="password123")
        self.business = Business.objects.create(
            user=self.user,
            name="Metric Shop",
            slug="metric-shop",
            onboarding_done=True,
        )
        self.product = Product.objects.create(
            business=self.business,
            name="Smart Gadget",
            price=250000,
            url="https://example.com/gadget",
            status="ACTIVE",
        )
        self.client = Client()

    def tearDown(self):
        cache.clear()

    def test_concurrent_clicks_increment_exactly_n(self):
        """Verify N concurrent record_click calls yield exactly N clicks (atomic F-expression)."""
        num_threads = 10
        errors = []

        def worker():
            try:
                ProductDailyMetric.record_click(self.product)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent workers raised errors: {errors}")

        metric = ProductDailyMetric.objects.get(product=self.product, date=timezone.now().date())
        self.assertEqual(metric.clicks_count, num_threads)

    def test_event_id_deduplication_prevents_double_counting(self):
        """Verify that retrying a click with the same event_id does not double count."""
        event_id = "click_evt_unique_123"

        # First attempt should increment count to 1
        count1 = ProductDailyMetric.record_click(self.product, event_id=event_id)
        self.assertEqual(count1, 1)

        # Retry attempt with identical event_id should be ignored and not increment
        count2 = ProductDailyMetric.record_click(self.product, event_id=event_id)
        self.assertEqual(count2, 1)

        metric = ProductDailyMetric.objects.get(product=self.product, date=timezone.now().date())
        self.assertEqual(metric.clicks_count, 1)

        # Different event_id should increment
        count3 = ProductDailyMetric.record_click(self.product, event_id="click_evt_different_456")
        self.assertEqual(count3, 2)
        metric.refresh_from_db()
        self.assertEqual(metric.clicks_count, 2)

    def test_product_click_view_integrates_record_click_and_deduplicates(self):
        """Verify /r/<id>/?ref=<event_id> view records clicks and honors deduplication."""
        url = reverse("product_click", args=[self.product.id])

        # First request with ref 'opp_lead_001'
        res1 = self.client.get(f"{url}?ref=opp_lead_001")
        self.assertEqual(res1.status_code, 302)
        self.assertEqual(res1["Location"], "https://example.com/gadget")

        metric = ProductDailyMetric.objects.get(product=self.product, date=timezone.now().date())
        self.assertEqual(metric.clicks_count, 1)

        # Immediate retry with same ref
        res2 = self.client.get(f"{url}?ref=opp_lead_001")
        self.assertEqual(res2.status_code, 302)

        metric.refresh_from_db()
        self.assertEqual(metric.clicks_count, 1)

        # Request with new ref
        res3 = self.client.get(f"{url}?ref=opp_lead_002")
        self.assertEqual(res3.status_code, 302)

        metric.refresh_from_db()
        self.assertEqual(metric.clicks_count, 2)

    def test_atomic_outreach_sent_increment(self):
        """Verify record_outreach_sent atomically increments outreach_sent_count."""
        num_threads = 5
        count_per_thread = 3
        errors = []

        def worker():
            try:
                ProductDailyMetric.record_outreach_sent(self.product, count=count_per_thread)
            except Exception as e:
                errors.append(e)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Concurrent workers raised errors: {errors}")

        metric = ProductDailyMetric.objects.get(product=self.product, date=timezone.now().date())
        self.assertEqual(metric.outreach_sent_count, num_threads * count_per_thread)

    def test_b2c_dead_counters_remain_untouched(self):
        """Verify views_count, orders_count, sales_amount are not modified by active click events."""
        today = timezone.now().date()
        ProductDailyMetric.objects.create(
            product=self.product,
            date=today,
            views_count=100,
            orders_count=5,
            sales_amount=1250000,
            clicks_count=0,
            outreach_sent_count=0,
        )

        ProductDailyMetric.record_click(self.product, date=today)
        ProductDailyMetric.record_outreach_sent(self.product, date=today, count=2)

        metric = ProductDailyMetric.objects.get(product=self.product, date=today)
        self.assertEqual(metric.clicks_count, 1)
        self.assertEqual(metric.outreach_sent_count, 2)
        # Historical B2C numbers preserved and untouched
        self.assertEqual(metric.views_count, 100)
        self.assertEqual(metric.orders_count, 5)
        self.assertEqual(metric.sales_amount, 1250000)
