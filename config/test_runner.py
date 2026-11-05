"""Django test runner used by `manage.py test` (settings.TEST_RUNNER)."""
import gc

from django.test.runner import DiscoverRunner


class GcDiscoverRunner(DiscoverRunner):
    def teardown_databases(self, old_config, **kwargs):
        # A psycopg connection that is only reachable through a reference cycle (e.g. a traceback frame) stays open
        # until the next GC run, and PostgreSQL refuses DROP DATABASE while any other session is connected
        # ("database test_... is being accessed by other users"). Collect it before the test database is dropped.
        gc.collect()
        return super().teardown_databases(old_config, **kwargs)
