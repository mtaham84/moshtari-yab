"""Django test runner used by `manage.py test` (settings.TEST_RUNNER)."""
import gc

from django.test.runner import DiscoverRunner


class GcDiscoverRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        from django.conf import settings

        # Production code must not guess whether it runs under tests; the runner switches these off explicitly.
        settings.INSTAGRAM_ANTIBAN_ENABLED = False   # no working-hours window, no human pauses
        settings.ACCOUNTS_POOL_FILE_SYNC = False     # tests must never rewrite the real pool file

    def teardown_databases(self, old_config, **kwargs):
        # A psycopg connection that is only reachable through a reference cycle (e.g. a traceback frame) stays open
        # until the next GC run, and PostgreSQL refuses DROP DATABASE while any other session is connected
        # ("database test_... is being accessed by other users"). Collect it before the test database is dropped.
        gc.collect()
        return super().teardown_databases(old_config, **kwargs)
