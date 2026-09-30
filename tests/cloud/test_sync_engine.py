"""Tests for revision-aware sync engine and privacy boundaries."""

import pytest
from core.cloud.sync import MemorySyncService
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.models import (
    MemoryKind,
    MemoryRecord,
    MemorySensitivity,
    MemoryStatus,
)


@pytest.mark.asyncio
async def test_memory_sync_push_and_pull() -> None:
    local_repo = SQLiteMemoryRepository(":memory:")
    remote_repo = SQLiteMemoryRepository(":memory:")
    sync_svc = MemorySyncService(local_repository=local_repo, remote_repository=remote_repo)

    # 1. Add normal record to local
    rec_normal = MemoryRecord(
        kind=MemoryKind.PREFERENCE,
        sensitivity=MemorySensitivity.NORMAL,
        content="User likes dark mode",
        fingerprint="fp_dark",
    )
    # Add local-only record to local
    rec_local_only = MemoryRecord(
        kind=MemoryKind.ROUTINE,
        sensitivity=MemorySensitivity.LOCAL_ONLY,
        content="Device specific configuration: port 8765",
        fingerprint="fp_local",
    )
    await local_repo.save(rec_normal)
    await local_repo.save(rec_local_only)

    # 2. Push to remote
    push_res = await sync_svc.push()
    assert push_res == 1  # Only NORMAL pushed!

    # Verify remote received normal but NOT local_only
    remote_normal = await remote_repo.get(rec_normal.id)
    assert remote_normal is not None
    assert remote_normal.content == "User likes dark mode"

    remote_local = await remote_repo.get(rec_local_only.id)
    assert remote_local is None

    # 3. Remote updates record
    remote_normal.content = "User strongly prefers dark mode"
    remote_normal.revision = 2
    await remote_repo.update(remote_normal)

    # 4. Pull to local
    pull_res = await sync_svc.pull()
    assert pull_res == 1

    local_updated = await local_repo.get(rec_normal.id)
    assert local_updated is not None
    assert local_updated.content == "User strongly prefers dark mode"
    assert local_updated.revision == 2


@pytest.mark.asyncio
async def test_memory_sync_tombstone_propagation() -> None:
    local_repo = SQLiteMemoryRepository(":memory:")
    remote_repo = SQLiteMemoryRepository(":memory:")
    sync_svc = MemorySyncService(local_repository=local_repo, remote_repository=remote_repo)

    rec = MemoryRecord(
        kind=MemoryKind.PROJECT,
        sensitivity=MemorySensitivity.NORMAL,
        content="Old active project Alpha",
        fingerprint="fp_alpha",
    )
    await local_repo.save(rec)
    await sync_svc.push()

    # Soft delete on local
    await local_repo.delete(rec.id, soft_delete=True)

    # Push tombstone to remote
    await sync_svc.push()

    remote_rec = await remote_repo.get(rec.id)
    assert remote_rec is not None
    assert remote_rec.status == MemoryStatus.DELETED
