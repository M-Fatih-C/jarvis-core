"""Abstract repository interface for memory persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID
from core.memory.models import (
    MemoryFilters,
    MemoryRecord,
    MemorySearchResult,
)


class MemoryRepositoryError(Exception):
    """Base exception for memory repository operations."""
    pass


class MemoryNotFoundError(MemoryRepositoryError):
    """Raised when a requested memory item does not exist."""
    pass


class MemoryConflictError(MemoryRepositoryError):
    """Raised when an update encounters a revision or concurrency conflict."""
    pass


class MemoryRepository(ABC):
    """Interface defining storage operations for memory records."""

    @abstractmethod
    async def get(self, memory_id: UUID) -> MemoryRecord | None:
        """Fetch memory record by UUID."""
        pass

    @abstractmethod
    async def save(self, memory: MemoryRecord) -> MemoryRecord:
        """Persist a new memory record."""
        pass

    @abstractmethod
    async def update(self, memory: MemoryRecord, expected_revision: int | None = None) -> MemoryRecord:
        """Update an existing record, optionally verifying expected revision for optimistic concurrency."""
        pass

    @abstractmethod
    async def delete(self, memory_id: UUID, soft_delete: bool = True) -> bool:
        """Mark as deleted (soft delete) or remove completely from store."""
        pass

    @abstractmethod
    async def list(self, filters: MemoryFilters | None = None) -> list[MemoryRecord]:
        """List memory records matching optional query filters."""
        pass

    @abstractmethod
    async def find_by_fingerprint(self, fingerprint: str) -> MemoryRecord | None:
        """Lookup an existing record by its normalized SHA-256 fingerprint."""
        pass

    async def find_by_source_id(self, source_id: str) -> MemoryRecord | None:
        """Lookup an existing record by its external source ID (e.g. seed id)."""
        return None

    @abstractmethod
    async def semantic_search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filters: MemoryFilters | None = None,
    ) -> list[MemorySearchResult]:
        """Perform vector similarity search against stored embeddings."""
        pass
