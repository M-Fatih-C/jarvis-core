"""Embedding providers for Jarvis vector memory retrieval."""

from core.memory.embeddings.base import EmbeddingProvider
from core.memory.embeddings.mock import DeterministicMockEmbeddingProvider
from core.memory.embeddings.sentence_transformer import LocalSentenceTransformerEmbeddingProvider


def get_embedding_provider(
    provider_type: str = "mock",
    model_name: str = "intfloat/multilingual-e5-small",
) -> EmbeddingProvider:
    """Factory creating the appropriate EmbeddingProvider."""
    if provider_type == "sentence_transformers":
        return LocalSentenceTransformerEmbeddingProvider(model_name=model_name)
    return DeterministicMockEmbeddingProvider()


__all__ = [
    "EmbeddingProvider",
    "DeterministicMockEmbeddingProvider",
    "LocalSentenceTransformerEmbeddingProvider",
    "get_embedding_provider",
]
