from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.discovery.tests import seller
from apps.discovery.x_query_report import combine_rows, query_report
from apps.products.models import Product


class QueryReportTests(SimpleTestCase):
    def test_scores_and_weights_use_only_current_seller(self):
        rows = combine_rows('9', [{'query': 'کنکور', 'runs': 4, 'seen': 100}],
                            [{'source_query': 'کنکور', 'leads': 2}],
                            [{'query': 'کنکور', 'business_ids': ['9'], 'score': 3, 'weight': .25, 'eligible': True},
                             {'query': 'private', 'business_ids': ['10'], 'score': 9, 'weight': .75, 'eligible': True}])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['weekly_score'], .5)
        self.assertEqual(rows[0]['weight_percent'], 25)
        self.assertEqual(rows[0]['score'], 3)

    def test_unsearched_generated_query_is_shown_without_invented_weekly_score(self):
        rows = combine_rows('9', [], [], [{'query': 'جدید', 'business_ids': ['9'], 'score': .5, 'weight': .1, 'eligible': True}])
        self.assertIsNone(rows[0]['weekly_score'])
        self.assertEqual(rows[0]['runs'], 0)

    def test_lead_query_without_retained_search_history_is_still_shown(self):
        rows = combine_rows('9', [], [{'source_query': 'کنکور', 'leads': 2}], [])
        self.assertEqual(rows[0]['leads'], 2)
        self.assertIsNone(rows[0]['weekly_score'])


class QueryReportDatabaseTests(TestCase):
    def test_shared_search_and_review_are_counted_once_and_old_posts_excluded(self):
        _, business = seller('query-report@example.com', 'Query report seller')
        products = [Product.objects.create(business=business, name=str(i), price=100) for i in range(2)]
        schema = 'query_report_' + uuid4().hex
        now = timezone.now()
        old = now - timedelta(days=8)
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE SCHEMA {schema}')
            cursor.execute(f'CREATE TABLE {schema}.x_search_runs (product_id text, query text, fetched int, ran_at timestamptz)')
            cursor.execute(f'CREATE TABLE {schema}.x_posts (tweet_id bigint, created_at timestamptz)')
            cursor.execute(f'CREATE TABLE {schema}.x_post_products (tweet_id bigint, product_id text, found_at timestamptz)')
            cursor.execute(f'CREATE TABLE {schema}.x_hit_results (message_id bigint, chat_id text, decided_at timestamptz, outcome text)')
            for tweet_id, date in [(1, now), (2, old)]:
                cursor.execute(f'INSERT INTO {schema}.x_posts VALUES (%s,%s)', [tweet_id, date])
            for product in products:
                cursor.execute(f'INSERT INTO {schema}.x_search_runs VALUES (%s,%s,20,%s)', [str(product.pk), 'کنکور', now])
                for tweet_id in [1, 2]:
                    cursor.execute(f'INSERT INTO {schema}.x_post_products VALUES (%s,%s,%s)', [tweet_id, str(product.pk), now])
                    cursor.execute(f'INSERT INTO {schema}.x_hit_results VALUES (%s,%s,%s,%s)',
                                   [tweet_id, f'x:p:{product.pk}', now, 'customer'])
        with patch('apps.discovery.x_query_report._crawler_schema', return_value=schema), \
                patch('apps.discovery.x_activity._engine_schema', return_value=schema), \
                patch('apps.discovery.x_query_report.x_collector_status', return_value=None):
            report = query_report(business)
        self.assertTrue(report['available'])
        self.assertEqual(report['rows'][0]['runs'], 1)
        self.assertEqual(report['rows'][0]['seen'], 20)
        self.assertEqual(report['unique_posts'], 1)
        self.assertEqual(report['reviewed_posts'], 1)
        self.assertFalse(report['replacement_ready'])
