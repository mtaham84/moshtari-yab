from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Wallet
from apps.discovery.tests import seller
from apps.discovery.x_activity import timeline
from apps.products.models import Product


class QueryResultsTests(TestCase):
    def setUp(self):
        self.user, self.business = seller('results@example.com', 'Results seller')
        _, other = seller('private-results@example.com', 'Private seller')
        self.owned = [Product.objects.create(business=self.business, name=str(i), price=100) for i in range(2)]
        foreign = Product.objects.create(business=other, name='private', price=100)
        self.schema = 'results_' + uuid4().hex
        now = timezone.now()
        with connection.cursor() as c:
            c.execute(f'CREATE SCHEMA {self.schema}')
            c.execute(f'''CREATE TABLE {self.schema}.x_posts (tweet_id bigint,author_handle text,author_name text,text text,
                url text,created_at timestamptz,kind text)''')
            c.execute(f'''CREATE TABLE {self.schema}.x_post_products
                (hit_id serial,tweet_id bigint,product_id text,query text,found_at timestamptz)''')
            c.execute(f'''CREATE TABLE {self.schema}.x_hit_results
                (chat_id text,message_id bigint,outcome text,reason text,need_id text,decided_at timestamptz)''')
            for tweet, text, date, pids, query in [
                (1, 'نتیجه مشترک خودم', now, self.owned, 'اشتراک GPT'),
                (2, 'متن محرمانه فروشگاه دیگر', now, [foreign], 'اشتراک GPT'),
                (3, 'پست قدیمی', now-timedelta(days=8), self.owned, 'اشتراک GPT'),
                (4, 'نتیجه کوئری دیگر', now, self.owned, 'اشتراک کلاد')]:
                c.execute(f'INSERT INTO {self.schema}.x_posts VALUES (%s,%s,%s,%s,%s,%s,%s)',
                          [tweet, 'buyer', 'Buyer', text, f'https://x.com/buyer/status/{tweet}', date, 'post'])
                for p in pids:
                    c.execute(f'INSERT INTO {self.schema}.x_post_products (tweet_id,product_id,query,found_at) VALUES (%s,%s,%s,%s)',
                              [tweet, str(p.pk), query, now])
        self.patch_c = patch('apps.discovery.x_activity._crawler_schema', return_value=self.schema)
        self.patch_e = patch('apps.discovery.x_activity._engine_schema', return_value=self.schema)
        self.patch_c.start()
        self.patch_e.start()
        self.addCleanup(self.patch_c.stop)
        self.addCleanup(self.patch_e.stop)

    def test_query_results_are_unique_recent_and_owned(self):
        rows = timeline(None, self.business, query='اشتراک GPT', limit=26)
        self.assertEqual([r['text'] for r in rows], ['نتیجه مشترک خودم'])
        self.assertEqual(timeline(None, self.business, query='اشتراک GPT', limit=26, offset=1), [])

    @override_settings(X_SELLER_UI=True)
    def test_seller_can_open_results_from_the_query_table(self):
        Wallet.objects.filter(business=self.business).update(balance_toman=100)
        self.client.force_login(self.user)
        with patch('apps.discovery.views.x_twitter.x_activity.product_stats', return_value={}), \
                patch('apps.discovery.x_query_report.query_report', return_value={'available': True, 'rows': []}):
            response = self.client.get(reverse('discovery:x_search'), {'query': 'اشتراک GPT'})
        self.assertContains(response, 'نتیجه مشترک خودم')
        self.assertNotContains(response, 'متن محرمانه فروشگاه دیگر')

    @override_settings(X_SELLER_UI=True)
    def test_zero_credit_cannot_use_results_to_receive_leads(self):
        Wallet.objects.filter(business=self.business).update(balance_toman=0)
        self.client.force_login(self.user)
        with patch('apps.discovery.views.x_twitter.x_activity.product_stats', return_value={}), \
                patch('apps.discovery.x_query_report.query_report', return_value={'available': True, 'rows': []}):
            response = self.client.get(reverse('discovery:x_search'), {'query': 'اشتراک GPT'})
        self.assertNotContains(response, 'نتیجه مشترک خودم')
        with patch('apps.discovery.views.x_twitter.x_activity.product_stats', return_value={}):
            product_page = self.client.get(reverse('discovery:x_product', args=[self.owned[0].pk]))
        self.assertNotContains(product_page, 'نتیجه مشترک خودم')
