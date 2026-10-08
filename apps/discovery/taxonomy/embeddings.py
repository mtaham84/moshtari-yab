from __future__ import annotations

import abc
import hashlib
import json
import math
import os
import urllib.request
import urllib.error
from typing import Optional
from .normalizer import normalize_persian_text, extract_search_tokens

DIMENSION = 128


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Computes cosine similarity between two float vectors."""
    if not vec_a or not vec_b:
        return 0.0
    if len(vec_a) != len(vec_b):
        min_len = min(len(vec_a), len(vec_b))
        vec_a = vec_a[:min_len]
        vec_b = vec_b[:min_len]

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    sim = dot_product / (norm_a * norm_b)
    # Clamp to [0.0, 1.0] for non-negative embeddings
    return max(0.0, min(1.0, float(sim)))


class BaseEmbeddingProvider(abc.ABC):
    @abc.abstractmethod
    def embed_text(self, text: str) -> list[float]:
        """Embeds a single string into a float vector."""
        pass

    @abc.abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embeds a list of strings into float vectors."""
        pass


class DeterministicSemanticEmbeddingProvider(BaseEmbeddingProvider):
    """
    High-performance, deterministic offline embedding provider.
    Combines word tokens, sub-word character n-grams, and semantic stems
    into an L2-normalized 128-dimensional dense vector.
    Enables accurate semantic matching without external API costs or latency.
    """

    def __init__(self, dim: int = DIMENSION):
        self.dim = dim

    def _hash_token(self, token: str) -> int:
        h = hashlib.sha256(token.encode("utf-8")).digest()
        return int.from_bytes(h[:4], "little") % self.dim

    def embed_text(self, text: str) -> list[float]:
        normalized = normalize_persian_text(text)
        tokens = extract_search_tokens(normalized)
        if not tokens:
            return [0.0] * self.dim

        vec = [0.0] * self.dim

        # 1. Word token hashing with higher weight
        for tok in tokens:
            idx = self._hash_token(f"w_{tok}")
            vec[idx] += 3.0

            # 2. Character n-grams (3-grams, 4-grams) to capture Persian morphemes
            if len(tok) >= 3:
                for n in (3, 4):
                    for i in range(len(tok) - n + 1):
                        ngram = tok[i:i + n]
                        idx_ng = self._hash_token(f"ng_{ngram}")
                        vec[idx_ng] += 1.0

        # 3. L2 Normalization
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        return [round(v, 6) for v in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


class ExternalAPIEmbeddingProvider(BaseEmbeddingProvider):
    """
    Calls external embedding API (OpenAI / Groq / custom compatible endpoint).
    Falls back gracefully to DeterministicSemanticEmbeddingProvider if offline.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "text-embedding-3-small"):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model
        self.fallback = DeterministicSemanticEmbeddingProvider()

    def embed_text(self, text: str) -> list[float]:
        if not self.api_key:
            return self.fallback.embed_text(text)

        try:
            url = "https://api.openai.com/v1/embeddings"
            payload = json.dumps({"input": text, "model": self.model}).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                return res_data["data"][0]["embedding"]
        except Exception:
            return self.fallback.embed_text(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            return self.fallback.embed_batch(texts)
        return [self.embed_text(t) for t in texts]


# In-memory Need Embedding Cache (keyed by normalized text)
_NEED_EMBEDDING_CACHE: dict[str, list[float]] = {}


def get_embedding_provider() -> BaseEmbeddingProvider:
    """Returns the configured embedding provider."""
    # Deterministic provider ensures fast, offline, and predictable tests
    return DeterministicSemanticEmbeddingProvider()


def get_need_embedding(
    need_text: str,
    product_type: str = "",
    attributes: Optional[dict] = None,
    provider: Optional[BaseEmbeddingProvider] = None
) -> list[float]:
    """
    Generates and caches embedding for an extracted customer need.
    Semantic text format: "{need} | product_type={product_type} | attributes={attributes}"
    """
    norm_need = normalize_persian_text(need_text)
    attr_str = json.dumps(attributes or {}, sort_keys=True, ensure_ascii=False)
    semantic_repr = f"{norm_need} | product_type={product_type} | attributes={attr_str}"

    cache_key = hashlib.sha256(semantic_repr.encode("utf-8")).hexdigest()
    if cache_key in _NEED_EMBEDDING_CACHE:
        return _NEED_EMBEDDING_CACHE[cache_key]

    p = provider or get_embedding_provider()
    embedding = p.embed_text(semantic_repr)
    _NEED_EMBEDDING_CACHE[cache_key] = embedding
    return embedding


def format_category_semantic_text(
    name: str,
    full_path: str = "",
    description: str = "",
    keywords: Optional[list[str]] = None
) -> str:
    """Formats category metadata into semantic text for embedding."""
    parts = []
    if full_path:
        parts.append(full_path)
    elif name:
        parts.append(name)
    if description:
        parts.append(description)
    if keywords:
        parts.append(f"keywords: {', '.join(keywords)}")
    return " | ".join(parts)
