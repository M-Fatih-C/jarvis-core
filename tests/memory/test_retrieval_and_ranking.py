"""Tests for memory ranking, context limits, and retrieval formatting."""

from datetime import datetime, timedelta, timezone
import pytest
from core.config.settings import Settings
from core.memory.crypto import InMemoryKeyProvider
from core.memory.embeddings.mock import DeterministicMockEmbeddingProvider
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.models import (
    MemoryKind,
    MemoryRecord,
    MemorySearchResult,
    MemorySensitivity,
)
from core.memory.ranking import MemoryRanker
from core.memory.retrieval import CONTEXT_SECURITY_BANNER, MemoryRetriever


@pytest.mark.asyncio
async def test_memory_ranking_heuristic() -> None:
    settings = Settings(
        ranking_weight_semantic=0.55,
        ranking_weight_importance=0.20,
        ranking_weight_recency=0.15,
        ranking_weight_confidence=0.10,
    )
    ranker = MemoryRanker(settings=settings)
    now = datetime.now(timezone.utc)

    # Hit 1: High semantic, low importance, updated today
    rec1 = MemoryRecord(
        kind=MemoryKind.PREFERENCE,
        sensitivity=MemorySensitivity.NORMAL,
        content="Prefers working late",
        importance=0.3,
        confidence=0.9,
        updated_at=now,
    )
    hit1 = MemorySearchResult(record=rec1, score=0.0, semantic_similarity=0.9)

    # Hit 2: Moderate semantic, very high importance, updated 2 months ago
    rec2 = MemoryRecord(
        kind=MemoryKind.EDUCATION,
        sensitivity=MemorySensitivity.NORMAL,
        content="Degree in Management Information Systems",
        importance=1.0,
        confidence=1.0,
        updated_at=now - timedelta(days=60),
    )
    hit2 = MemorySearchResult(record=rec2, score=0.0, semantic_similarity=0.6)

    ranked = ranker.rank([hit1, hit2], now=now)
    assert len(ranked) == 2
    # Verify score ordering
    assert ranked[0].score >= ranked[1].score


@pytest.mark.asyncio
async def test_memory_retrieval_context_formatting_and_security_banner() -> None:
    repo = SQLiteMemoryRepository(":memory:")
    embedder = DeterministicMockEmbeddingProvider(dimensions=384)
    retriever = MemoryRetriever(
        repository=repo,
        embedding_provider=embedder,
        settings=Settings(max_memory_context_chars=1000),
    )

    rec = MemoryRecord(
        kind=MemoryKind.PREFERENCE,
        sensitivity=MemorySensitivity.NORMAL,
        content="User prefers side-project work after 18:00 on weekdays.",
        confidence=0.98,
        embedding=await embedder.embed_document("side project work evening"),
    )
    await repo.save(rec)

    hits = await retriever.retrieve("side project")
    assert len(hits) == 1

    formatted_context = retriever.format_context_block(hits)
    assert "<memory_context>" in formatted_context
    assert CONTEXT_SECURITY_BANNER in formatted_context
    assert "[preference | confidence=0.98 | status=user_reported] User prefers side-project work after 18:00 on weekdays." in formatted_context
    assert "</memory_context>" in formatted_context
