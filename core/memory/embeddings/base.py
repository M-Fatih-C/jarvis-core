"""Abstract interface for generating local text embeddings."""

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Abstract provider for generating vector representations of queries and documents."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Dimensionality of the vector space."""
        pass

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Generate normalized embedding for a search query string."""
        pass

    @abstractmethod
    async def embed_document(self, text: str) -> list[float]:
        """Generate normalized embedding for a document or memory record."""
        pass
