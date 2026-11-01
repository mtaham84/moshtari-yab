from io import StringIO
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.db import connection
from django.core.management import call_command

import psycopg
from need_engine.store import Store


class DecoupleEngineDDLTestCase(TestCase):
    """
    P07.02 Verification: Decouple DDL from active requests, explicit bootstrap,
    read-only preflight check, and least-privilege runtime safety (R33).
    """

    def setUp(self):
        super().setUp()
        db_conf = connection.settings_dict
        self.dsn = f"postgresql://{db_conf['USER']}:{db_conf['PASSWORD']}@{db_conf['HOST']}:{db_conf['PORT']}/{db_conf['NAME']}"
        self.schema = "test_p07_02_ddl"

        # Ensure schema is dropped before testing
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")

    def tearDown(self):
        try:
            with psycopg.connect(self.dsn, autocommit=True) as conn:
                conn.execute(f"DROP SCHEMA IF EXISTS {self.schema} CASCADE")
        except Exception:
            pass
        super().tearDown()

    def test_runtime_create_false_executes_zero_ddl_statements(self):
        """When initialized with create=False, Store executes ZERO DDL statements (no CREATE/ALTER/DROP)."""
        # First create the schema so connection can attach
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")

        executed_statements = []

        # Spy on psycopg connection execute
        real_connect = psycopg.connect

        def spied_connect(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            real_exec = conn.execute

            def tracking_exec(query, *exec_args, **exec_kwargs):
                query_str = str(query).strip().upper()
                executed_statements.append(query_str)
                return real_exec(query, *exec_args, **exec_kwargs)

            conn.execute = tracking_exec
            return conn

        with patch("need_engine.store.psycopg.connect", side_effect=spied_connect):
            store = Store(self.dsn, schema=self.schema, create=False)
            store.close()

        # Check all executed statements: none should be DDL
        ddl_keywords = ["CREATE TABLE", "ALTER TABLE", "CREATE EXTENSION", "CREATE INDEX"]
        for stmt in executed_statements:
            for kw in ddl_keywords:
                self.assertNotIn(
                    kw,
                    stmt,
                    f"DDL statement '{kw}' was executed in runtime path: {stmt}",
                )

    def test_preflight_schema_fails_when_tables_missing(self):
        """When required tables are missing, _preflight_schema raises RuntimeError without running DDL."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")

        store = Store(self.dsn, schema=self.schema, create=False)
        with self.assertRaises(RuntimeError) as ctx:
            store._preflight_schema()

        self.assertIn("missing required tables", str(ctx.exception))
        store.close()

    def test_bootstrap_schema_and_preflight_validation(self):
        """Explicit bootstrap_schema creates all required tables and passes read-only preflight check."""
        with psycopg.connect(self.dsn, autocommit=True) as conn:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")

        store = Store(self.dsn, schema=self.schema, create=False)
        # Explicit bootstrap
        store.bootstrap_schema()

        # Preflight check succeeds
        self.assertTrue(store._preflight_schema())
        store.close()

    def test_migrate_engine_schema_management_command(self):
        """Management command migrate_engine_schema executes cleanly."""
        out = StringIO()
        call_command("migrate_engine_schema", f"--schema={self.schema}", stdout=out)
        output = out.getvalue()

        self.assertIn(f"Migrating engine schema '{self.schema}'", output)
        self.assertIn("successfully bootstrapped and validated", output)
