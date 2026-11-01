"""
Unit test suite for Embedder Circuit Breaker and Quota Recovery (P05.06 / R18).
"""

import time
from unittest import mock
import numpy as np
from django.test import SimpleTestCase

from need_engine.config import EngineConfig
from need_engine.embeddings import Embedder
from need_engine.llm import QuotaExhausted


class EmbedderRecoveryTestCase(SimpleTestCase):
    """
    Validates P05.06:
    - Timed circuit breaker (exhausted_until instead of static boolean)
    - Injectable clock
    - Self-healing recovery past reset time
    - Anti-stampede probe defense
    """

    def setUp(self):
        self.cfg = EngineConfig()
        self.cfg.embed_backend = "hash"
        self.current_time = 1700000000.0  # Simulated epoch

    def fake_clock(self) -> float:
        return self.current_time

    def test_quota_exhausted_trips_circuit_breaker(self):
        """
        R18: QuotaExhausted trips circuit breaker until calculated reset time.
        """
        embedder = Embedder(self.cfg, llm=mock.MagicMock(), clock=self.fake_clock)
        self.assertFalse(embedder.exhausted)

        # Trigger QuotaExhausted
        with mock.patch.object(embedder, "_hash_vec", side_effect=QuotaExhausted("429 Daily Limit")):
            res = embedder.encode(["Hello World"])
            self.assertEqual(res.shape, (1, 0))

        # Circuit is now tripped
        self.assertTrue(embedder.exhausted)
        self.assertGreater(embedder.exhausted_until, self.current_time)

        # Subsequent call before reset is bypassed immediately without calling _hash_vec
        with mock.patch.object(embedder, "_hash_vec") as mock_hash:
            res2 = embedder.encode(["Another Text"])
            self.assertEqual(res2.shape, (1, 0))
            mock_hash.assert_not_called()

    def test_clock_advance_past_reset_triggers_probe_and_recovery(self):
        """
        R18: When injectable clock advances past reset time, probe succeeds and circuit recovers.
        """
        embedder = Embedder(self.cfg, llm=mock.MagicMock(), clock=self.fake_clock)

        # Manually trip circuit with reset set to current_time + 300s
        embedder.exhausted_until = self.current_time + 300.0
        self.assertTrue(embedder.exhausted)

        # Advance clock by 301 seconds (past reset threshold)
        self.current_time += 301.0
        self.assertFalse(embedder.exhausted)

        # Calling encode now triggers probe and succeeds
        res = embedder.encode(["Clean Text"])
        self.assertGreater(res.shape[1], 0)
        self.assertEqual(embedder.exhausted_until, 0.0)
        self.assertFalse(embedder.exhausted)

    def test_probe_anti_stampede_protection(self):
        """
        R18: While probe is in-flight, concurrent calls do not stampede the backend.
        """
        embedder = Embedder(self.cfg, llm=mock.MagicMock(), clock=self.fake_clock)
        embedder.exhausted_until = self.current_time - 10.0  # Past reset time
        embedder._probing = True  # Simulate probe in flight by thread A

        with mock.patch.object(embedder, "_hash_vec") as mock_hash:
            res = embedder.encode(["Concurrent Text"])
            # Thread B is protected from stampeding
            self.assertEqual(res.shape, (1, 0))
            mock_hash.assert_not_called()

    def test_failed_probe_resets_exhausted_until(self):
        """
        R18: If recovery probe encounters QuotaExhausted again, circuit remains open.
        """
        embedder = Embedder(self.cfg, llm=mock.MagicMock(), clock=self.fake_clock)
        embedder.exhausted_until = self.current_time - 10.0  # Past reset time

        with mock.patch.object(embedder, "_hash_vec", side_effect=QuotaExhausted("Still Exhausted")):
            res = embedder.encode(["Probe Attempt"])
            self.assertEqual(res.shape, (1, 0))

        # Circuit re-trips into future reset time
        self.assertTrue(embedder.exhausted)
        self.assertGreater(embedder.exhausted_until, self.current_time)
        self.assertFalse(embedder._probing)
