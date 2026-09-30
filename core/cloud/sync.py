"""Revision-aware synchronization between local SQLite store and Firestore remote repository."""

from __future__ import annotations

from core.logging.setup import get_logger
from core.memory.models import MemoryFilters, MemoryRecord, MemorySensitivity, MemoryStatus
from core.memory.repository import MemoryConflictError, MemoryRepository

logger = get_logger("jarvis.cloud.sync")


class MemorySyncService:
    """Manages bidirectional revision-aware synchronization with tombstone support."""

    def __init__(
        self,
        local_repository: MemoryRepository,
        remote_repository: MemoryRepository,
    ) -> None:
        self._local = local_repository
        self._remote = remote_repository

    async def pull(self) -> int:
        """Pull remote changes from Firestore to local SQLite repository."""
        remote_records = await self._remote.list(filters=None)
        pulled_count = 0

        for r_rec in remote_records:
            # LOCAL_ONLY is never remote anyway
            l_rec = await self._local.get(r_rec.id)

            if l_rec is None:
                # New remote record -> save locally
                await self._local.save(r_rec)
                pulled_count += 1
            elif r_rec.revision > l_rec.revision:
                # Remote is newer -> update local
                await self._local.update(r_rec)
                pulled_count += 1
            elif r_rec.status == MemoryStatus.DELETED and l_rec.status != MemoryStatus.DELETED:
                # Remote tombstone -> delete locally
                await self._local.delete(r_rec.id, soft_delete=True)
                pulled_count += 1

        logger.info("memory_sync_pull_completed", pulled=pulled_count)
        return pulled_count

    async def push(self) -> int:
        """Push local changes to Firestore remote repository."""
        local_records = await self._local.list(filters=None)
        pushed_count = 0

        for l_rec in local_records:
            # LOCAL_ONLY records MUST NEVER be pushed to cloud
            if l_rec.sensitivity == MemorySensitivity.LOCAL_ONLY:
                continue

            r_rec = await self._remote.get(l_rec.id)

            if r_rec is None:
                # New local record -> save remotely
                await self._remote.save(l_rec)
                pushed_count += 1
            elif l_rec.revision > r_rec.revision:
                # Local is newer -> update remote with optimistic concurrency check
                try:
                    await self._remote.update(l_rec, expected_revision=r_rec.revision)
                    pushed_count += 1
                except MemoryConflictError:
                    logger.warning("memory_sync_push_conflict", memory_id=str(l_rec.id))
            elif l_rec.status == MemoryStatus.DELETED and r_rec.status != MemoryStatus.DELETED:
                # Local tombstone -> soft delete remote
                await self._remote.delete(l_rec.id, soft_delete=True)
                pushed_count += 1

        logger.info("memory_sync_push_completed", pushed=pushed_count)
        return pushed_count

    async def sync(self) -> dict[str, int]:
        """Perform full bidirectional synchronization (pull, then push)."""
        pulled = await self.pull()
        pushed = await self.push()
        return {"pulled": pulled, "pushed": pushed}
