from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from apps.discovery.management.commands.purge_x_queries import purge_legacy_term_cache


class LegacyXQueryCacheCleanupTests(SimpleTestCase):
    def _connection(self, cursor):
        fake = MagicMock()
        fake.cursor.return_value.__enter__.return_value = cursor
        return fake

    def test_dry_run_keeps_family_cache_and_does_not_delete(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [("x_qterms:17",), ("x_qterms:f17-a1b2c3d4",)]
        with patch("apps.discovery.management.commands.purge_x_queries.connection", self._connection(cursor)):
            count = purge_legacy_term_cache(dry_run=True)
        self.assertEqual(count, 1)
        self.assertEqual(cursor.execute.call_count, 1)
        self.assertNotIn("DELETE", cursor.execute.call_args.args[0])

    def test_execute_deletes_only_legacy_keys(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [("x_qterms:17",), ("x_qterms:f17-a1b2c3d4",)]
        with patch("apps.discovery.management.commands.purge_x_queries.connection", self._connection(cursor)):
            count = purge_legacy_term_cache(dry_run=False)
        self.assertEqual(count, 1)
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertEqual(cursor.execute.call_args.args[1], [["x_qterms:17"]])
