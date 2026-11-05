import os
from unittest.mock import patch

import numpy as np
from django.test import SimpleTestCase

import apps.discovery.semantic_moderation as sm


class GibberishHeuristicTests(SimpleTestCase):
    def test_repeated_character_gibberish_is_detected(self):
        self.assertTrue(sm.is_gibberish_heuristics("ههههههههههه")[0])
        self.assertFalse(sm.is_gibberish_heuristics("سلاممممم قیمت این مدل چنده؟")[0])
        res = sm.check_comment_semantic_moderation("ااااااااااا")
        self.assertTrue(res["is_rejected"])
        self.assertEqual(res["category"], "gibberish_and_meaningless")


class EmbeddingFlagTests(SimpleTestCase):
    @patch.dict(os.environ, {"SEMANTIC_MODERATION_EMBEDDINGS": ""})
    def test_embeddings_are_off_by_default(self):
        with patch.object(sm, "_embedding_moderation", side_effect=AssertionError("embeddings must stay off")):
            res = sm.check_comment_semantic_moderation("سلام قیمت این مدل رو دایرکت بفرستید")
        self.assertFalse(res["is_rejected"])
        self.assertIsNone(sm._get_embedder())

    @patch.dict(os.environ, {"SEMANTIC_MODERATION_EMBEDDINGS": "1"})
    def test_embedding_check_runs_when_enabled(self):
        class FakeEmbedder:
            def encode(self, texts):
                return np.array([[1.0, 0.0] for _ in texts])

        with patch.object(sm, "_get_embedder", return_value=FakeEmbedder()), \
             patch.object(sm, "_init_anchor_vectors", return_value=True), \
             patch.object(sm, "_CACHED_ANCHOR_VECTORS", {"yellow_spam_and_bots": np.array([[1.0, 0.0]])}), \
             patch.object(sm, "_CACHED_POSITIVE_VECTORS", np.array([[0.0, 1.0]])):
            res = sm.check_comment_semantic_moderation("افزایش فالوور تضمینی")
        self.assertTrue(res["is_rejected"])
        self.assertEqual(res["category"], "yellow_spam_and_bots")
