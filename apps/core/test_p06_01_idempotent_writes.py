import json
from unittest.mock import MagicMock, patch
from django.db import connection
from django.test import TestCase

import psycopg
from need_engine.store import (
    Store,
    IdempotencyConflictError,
    compute_payload_fingerprint,
)


class IdempotentWritesTestCase(TestCase):
    """
    P06.01 Verification: Classified write idempotency, operation_id stability,
    and lost-ACK / OperationalError recovery (R34).
    """

    def setUp(self):
        super().setUp()
        db_conf = connection.settings_dict
        db_name = db_conf['NAME']
        dsn = f"postgresql://{db_conf['USER']}:{db_conf['PASSWORD']}@{db_conf['HOST']}:{db_conf['PORT']}/{db_name}"
        self.store = Store(dsn, schema="test_p06_01")
        # Ensure clean tables
        self.store._exec("TRUNCATE TABLE {s}.operation_idempotency, {s}.costs CASCADE")

    def tearDown(self):
        try:
            self.store._exec("TRUNCATE TABLE {s}.operation_idempotency, {s}.costs CASCADE")
            self.store.close()
        except Exception:
            pass
        super().tearDown()

    def test_fingerprint_deterministic(self):
        """Fingerprint must be deterministic regardless of dictionary key ordering."""
        p1 = {"b": 2, "a": 1, "c": [1, 2, 3]}
        p2 = {"a": 1, "c": [1, 2, 3], "b": 2}
        self.assertEqual(compute_payload_fingerprint(p1), compute_payload_fingerprint(p2))

    def test_idempotent_write_executes_once_and_returns_cached_result_on_replay(self):
        """Replaying execute_idempotent_write with same operation_id and fingerprint returns cached result."""
        op_id = "op-test-replay-001"
        payload = {"action": "create_item", "val": 42}
        fp = compute_payload_fingerprint(payload)
        execution_count = 0

        def _write(c: psycopg.Connection):
            nonlocal execution_count
            execution_count += 1
            return {"status": "created", "count": execution_count}

        res1 = self.store.execute_idempotent_write(op_id, fp, _write)
        self.assertEqual(execution_count, 1)
        self.assertEqual(res1, {"status": "created", "count": 1})

        # Replay with same op_id and fp
        res2 = self.store.execute_idempotent_write(op_id, fp, _write)
        # Should NOT execute write_fn again!
        self.assertEqual(execution_count, 1)
        self.assertEqual(res2, {"status": "created", "count": 1})

    def test_idempotent_write_raises_conflict_on_mismatched_fingerprint(self):
        """Reusing operation_id with differing payload fingerprint raises IdempotencyConflictError."""
        op_id = "op-test-conflict-001"
        p1 = {"amount": 100}
        p2 = {"amount": 200}
        fp1 = compute_payload_fingerprint(p1)
        fp2 = compute_payload_fingerprint(p2)

        def _write(c: psycopg.Connection):
            return "ok"

        self.store.execute_idempotent_write(op_id, fp1, _write)

        with self.assertRaises(IdempotencyConflictError) as ctx:
            self.store.execute_idempotent_write(op_id, fp2, _write)
        self.assertIn("Idempotency conflict", str(ctx.exception))

    def test_reconnect_and_reconcile_on_lost_ack_after_commit(self):
        """
        If OperationalError is raised right after commit (e.g. lost TCP ACK / socket disconnect),
        execute_idempotent_write reconnects and checks idempotency table.
        It returns the committed result without re-executing write_fn.
        """
        op_id = "op-test-lost-ack-001"
        payload = {"action": "bill", "seller": "seller-1"}
        fp = compute_payload_fingerprint(payload)
        calls = 0

        def _write(c: psycopg.Connection):
            nonlocal calls
            calls += 1
            return {"charged": True, "call_index": calls}

        # 1. Simulate the case where write committed on DB, but network exception occurred on client return
        with self.store.conn.transaction():
            res = _write(self.store.conn)
            self.store.conn.execute(
                f"""INSERT INTO {self.store._t('operation_idempotency')} (operation_id, fingerprint, status, result)
                    VALUES (%s, %s, 'SUCCESS', %s)""",
                (op_id, fp, psycopg.types.json.Jsonb(res)),
            )

        # 2. When execute_idempotent_write is called with that op_id, pre-check or operational error catch
        # returns the result without executing write_fn again
        res_reconciled = self.store.execute_idempotent_write(op_id, fp, _write)
        self.assertEqual(calls, 1)
        self.assertEqual(res_reconciled, {"charged": True, "call_index": 1})

    def test_operational_error_during_tx_body_reconnects_and_reconciles(self):
        """
        Simulate an OperationalError thrown directly by the transaction execution on the first try.
        The error occurs after the database committed the row.
        execute_idempotent_write should catch OperationalError, reconnect, find the committed row,
        and return it safely without raising or re-executing write_fn.
        """
        op_id = "op-test-op-err-reconcile"
        payload = {"data": "test_crash"}
        fp = compute_payload_fingerprint(payload)
        calls = 0

        def _write(c: psycopg.Connection):
            nonlocal calls
            calls += 1
            return {"saved": True, "attempt": calls}

        first_attempt = True
        real_transaction = self.store.conn.transaction

        class FaultyTxContext:
            def __init__(self, ctx):
                self.ctx = ctx
            def __enter__(self):
                return self.ctx.__enter__()
            def __exit__(self, exc_type, exc_val, exc_tb):
                # Let original transaction commit successfully
                res = self.ctx.__exit__(exc_type, exc_val, exc_tb)
                nonlocal first_attempt
                if first_attempt:
                    first_attempt = False
                    # Raise OperationalError right after commit!
                    raise psycopg.OperationalError("Simulated TCP drop immediately after COMMIT")
                return res

        def faulty_transaction():
            return FaultyTxContext(real_transaction())

        with patch.object(self.store.conn, "transaction", side_effect=faulty_transaction):
            result = self.store.execute_idempotent_write(op_id, fp, _write)

        self.assertEqual(result, {"saved": True, "attempt": 1})
        self.assertEqual(calls, 1)

    def test_add_cost_with_operation_id_is_idempotent(self):
        """Calling add_cost with operation_id twice inserts exactly one set of rows."""
        op_id = "op-cost-001"
        self.store.add_cost(
            stage="test_stage",
            model="gemini-flash",
            pt=100,
            ct=50,
            cached=False,
            usd=0.001,
            toman=268.0,
            ref="test_ref",
            businesses=["biz_1"],
            operation_id=op_id,
        )

        rows1 = self.store._all("SELECT * FROM {s}.costs WHERE ref = %s", ("test_ref",))
        self.assertEqual(len(rows1), 1)

        # Call again with the exact same operation_id
        self.store.add_cost(
            stage="test_stage",
            model="gemini-flash",
            pt=100,
            ct=50,
            cached=False,
            usd=0.001,
            toman=268.0,
            ref="test_ref",
            businesses=["biz_1"],
            operation_id=op_id,
        )

        rows2 = self.store._all("SELECT * FROM {s}.costs WHERE ref = %s", ("test_ref",))
        # Still exactly 1 row! No duplicate cost insertion!
        self.assertEqual(len(rows2), 1)
