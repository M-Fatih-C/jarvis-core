"""Two-process acceptance test proving cross-process SQLite persistence and retrieval."""

from pathlib import Path
import subprocess
import sys


def test_two_process_sqlite_memory_persistence(tmp_path: Path) -> None:
    db_file = tmp_path / "proc_test_memory.db"
    db_path_str = str(db_file)

    # ------------------------------------------------------------------------
    # PROCESS A: Store preference in SQLite and terminate process
    # ------------------------------------------------------------------------
    script_a = f"""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.embeddings.mock import DeterministicMockEmbeddingProvider
from core.memory.models import MemoryRecord, MemoryKind, MemorySensitivity, MemorySourceType, MemoryStatus

async def main():
    repo = SQLiteMemoryRepository("{db_path_str}")
    embedder = DeterministicMockEmbeddingProvider(dimensions=384)
    now = datetime.now(timezone.utc)
    embedding = await embedder.embed_document("User prefers side project work after 18:00 on weekdays.")

    rec = MemoryRecord(
        id=uuid4(),
        kind=MemoryKind.PREFERENCE,
        sensitivity=MemorySensitivity.NORMAL,
        content="User prefers side project work after 18:00 on weekdays.",
        structured={{"domain": "schedule", "after": "18:00"}},
        confidence=0.98,
        importance=0.85,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="proc_test_fp_1",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        embedding=embedding,
        embedding_model="mock-384",
    )
    await repo.save(rec)
    print("PROCESS_A_SAVED:" + str(rec.id))

asyncio.run(main())
"""

    res_a = subprocess.run(
        [sys.executable, "-c", script_a],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "PROCESS_A_SAVED:" in res_a.stdout
    saved_id = res_a.stdout.strip().split("PROCESS_A_SAVED:")[1].strip()

    # ------------------------------------------------------------------------
    # PROCESS B: Launch separate process, open same SQLite DB, retrieve memory
    # ------------------------------------------------------------------------
    script_b = f"""
import asyncio
from uuid import UUID
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.embeddings.mock import DeterministicMockEmbeddingProvider
from core.memory.retrieval import MemoryRetriever
from core.memory.ranking import MemoryRanker
from core.memory.crypto import InMemoryKeyProvider
from core.config.settings import Settings

async def main():
    repo = SQLiteMemoryRepository("{db_path_str}")
    embedder = DeterministicMockEmbeddingProvider(dimensions=384)
    key_prov = InMemoryKeyProvider()
    settings = Settings()
    ranker = MemoryRanker(settings=settings)
    retriever = MemoryRetriever(
        repository=repo,
        embedding_provider=embedder,
        ranker=ranker,
        key_provider=key_prov,
        settings=settings,
    )

    # 1. Direct get by ID
    rec = await repo.get(UUID("{saved_id}"))
    assert rec is not None, "Memory not found in Process B!"
    assert rec.content == "User prefers side project work after 18:00 on weekdays."
    assert rec.embedding is not None
    assert len(rec.embedding) == 384

    # 2. Semantic retrieval from fresh query
    hits = await retriever.retrieve("Yarın yan proje zamanı", limit=3)
    assert len(hits) >= 1, "Semantic retrieval returned no hits in Process B!"
    assert hits[0].record.id == UUID("{saved_id}")
    print("PROCESS_B_RETRIEVED_OK")

asyncio.run(main())
"""

    res_b = subprocess.run(
        [sys.executable, "-c", script_b],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "PROCESS_B_RETRIEVED_OK" in res_b.stdout
