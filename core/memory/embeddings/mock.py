"""Deterministic mock embedding provider for fast, dependency-free testing and CI."""

import hashlib
import math
import re
from core.memory.embeddings.base import EmbeddingProvider


class DeterministicMockEmbeddingProvider(EmbeddingProvider):
    """Generates deterministic, unit-normalized vector embeddings using token hashing.
    
    Ensures that identical or overlapping token sets yield positive cosine similarity,
    making it ideal for unit and integration testing without requiring GPU or model downloads.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _generate_vector(self, text: str) -> list[float]:
        # Initialize zero vector
        vector = [0.0] * self._dimensions
        words = re.findall(r"\w+", text.lower())

        if not words:
            # Deterministic baseline for empty string
            words = ["empty"]

        for word in words:
            # Deterministic 32-bit hash per word
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            idx = h % self._dimensions
            # Directional weight from secondary hash bits
            weight = 1.0 if ((h >> 8) & 1) == 0 else -1.0
            vector[idx] += weight

        # Normalize to unit length (L2 norm)
        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0.0:
            vector = [x / norm for x in vector]
        else:
            vector[0] = 1.0

        return vector

    async def embed_query(self, text: str) -> list[float]:
        return self._generate_vector(f"query: {text}")

    async def embed_document(self, text: str) -> list[float]:
        return self._generate_vector(f"passage: {text}")
