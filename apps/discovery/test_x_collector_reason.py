from django.test import SimpleTestCase
from unittest.mock import patch

from apps.discovery.x_activity import collector_line


class CollectorReasonTests(SimpleTestCase):
    def test_daily_limit_is_not_reported_as_running(self):
        with patch('apps.billing.x_stats.x_collector_status', return_value={
                'status': 'DAILY_CAP_REACHED', 'age': 10, 'updated': 'اکنون'}):
            result = collector_line()
        self.assertFalse(result['ok'])
        self.assertIn('سقف', result['text'])

    def test_no_accounts_is_not_reported_as_running(self):
        with patch('apps.billing.x_stats.x_collector_status', return_value={
                'status': 'ALL_ACCOUNTS_UNAVAILABLE', 'age': 10, 'updated': 'اکنون'}):
            result = collector_line()
        self.assertFalse(result['ok'])
        self.assertIn('حساب', result['text'])
