"""Local SQLite implementation of the MemoryRepository interface."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
from typing import Any
from uuid import UUID
from core.logging.setup import get_logger
from core.memory.models import (
    MemoryFilters,
    MemoryKind,
    MemoryRecord,
    MemorySearchResult,
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
)
from core.memory.repository import (
    MemoryConflictError,
    MemoryNotFoundError,
    MemoryRepository,
)

logger = get_logger("jarvis.memory.sqlite")


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if len(v1) != len(v2) or not v1:
        return 0.0
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 0.0
    return float(dot / (norm1 * norm2))


class SQLiteMemoryRepository(MemoryRepository):
    """Thread-safe SQLite store for local caching, PRIVATE copies, and offline operations."""

    def __init__(self, db_path: str = ":memory:") -> None:
        if db_path != ":memory:":
            expanded_path = Path(os.path.expanduser(db_path)).resolve()
            expanded_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path = str(expanded_path)
        else:
            self._db_path = ":memory:"

        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        return self._conn

    def _init_db(self) -> None:
        """Create table and indexes if they do not exist."""
        with self._conn:
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                content TEXT,
                encrypted_content TEXT,
                structured_json TEXT NOT NULL DEFAULT '{}',
                tags_json TEXT NOT NULL DEFAULT '[]',
                source_type TEXT NOT NULL,
                source_ref TEXT,
                confidence REAL NOT NULL,
                importance REAL NOT NULL,
                subject TEXT,
                predicate TEXT,
                value_json TEXT,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_accessed_at TEXT,
                expires_at TEXT,
                embedding_json TEXT,
                embedding_model TEXT,
                revision INTEGER NOT NULL DEFAULT 1,
                supersedes TEXT,
                training_eligible INTEGER NOT NULL DEFAULT 0,
                schema_version INTEGER NOT NULL DEFAULT 1,
                deleted_at TEXT
            );
            """)
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status_kind ON memories (status, kind);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_fingerprint ON memories (fingerprint);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_subject_predicate ON memories (subject, predicate);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_updated_at ON memories (updated_at);")

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        created_at = datetime.fromisoformat(row["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        updated_at = datetime.fromisoformat(row["updated_at"])
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        last_accessed = None
        if row["last_accessed_at"]:
            last_accessed = datetime.fromisoformat(row["last_accessed_at"])
            if last_accessed.tzinfo is None:
                last_accessed = last_accessed.replace(tzinfo=timezone.utc)

        expires_at = None
        if row["expires_at"]:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

        deleted_at = None
        if row["deleted_at"]:
            deleted_at = datetime.fromisoformat(row["deleted_at"])
            if deleted_at.tzinfo is None:
                deleted_at = deleted_at.replace(tzinfo=timezone.utc)

        embedding = None
        if row["embedding_json"]:
            try:
                embedding = json.loads(row["embedding_json"])
            except Exception:
                embedding = None

        value = None
        if row["value_json"]:
            try:
                value = json.loads(row["value_json"])
            except Exception:
                value = row["value_json"]

        return MemoryRecord(
            id=UUID(row["id"]),
            kind=MemoryKind(row["kind"]),
            sensitivity=MemorySensitivity(row["sensitivity"]),
            content=row["content"],
            encrypted_content=row["encrypted_content"],
            structured=json.loads(row["structured_json"]) if row["structured_json"] else {},
            tags=json.loads(row["tags_json"]) if row["tags_json"] else [],
            source_type=MemorySourceType(row["source_type"]),
            source_ref=row["source_ref"],
            confidence=float(row["confidence"]),
            importance=float(row["importance"]),
            subject=row["subject"],
            predicate=row["predicate"],
            value=value,
            fingerprint=row["fingerprint"],
            status=MemoryStatus(row["status"]),
            created_at=created_at,
            updated_at=updated_at,
            last_accessed_at=last_accessed,
            expires_at=expires_at,
            embedding=embedding,
            embedding_model=row["embedding_model"],
            revision=int(row["revision"]),
            supersedes=UUID(row["supersedes"]) if row["supersedes"] else None,
            training_eligible=bool(row["training_eligible"]),
            schema_version=int(row["schema_version"]),
            deleted_at=deleted_at,
        )

    async def get(self, memory_id: UUID) -> MemoryRecord | None:
        async with self._lock:
            cursor = self._conn.execute("SELECT * FROM memories WHERE id = ?", (str(memory_id),))
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None

    async def save(self, memory: MemoryRecord) -> MemoryRecord:
        async with self._lock:
            with self._conn:
                self._conn.execute("""
                INSERT INTO memories (
                    id, kind, sensitivity, content, encrypted_content,
                    structured_json, tags_json, source_type, source_ref,
                    confidence, importance, subject, predicate, value_json,
                    fingerprint, status, created_at, updated_at,
                    last_accessed_at, expires_at, embedding_json,
                    embedding_model, revision, supersedes,
                    training_eligible, schema_version, deleted_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """, (
                    str(memory.id),
                    memory.kind.value,
                    memory.sensitivity.value,
                    memory.content,
                    memory.encrypted_content,
                    json.dumps(memory.structured, ensure_ascii=False),
                    json.dumps(memory.tags, ensure_ascii=False),
                    memory.source_type.value,
                    memory.source_ref,
                    memory.confidence,
                    memory.importance,
                    memory.subject,
                    memory.predicate,
                    json.dumps(memory.value, ensure_ascii=False) if memory.value is not None else None,
                    memory.fingerprint,
                    memory.status.value,
                    memory.created_at.isoformat(),
                    memory.updated_at.isoformat(),
                    memory.last_accessed_at.isoformat() if memory.last_accessed_at else None,
                    memory.expires_at.isoformat() if memory.expires_at else None,
                    json.dumps(memory.embedding) if memory.embedding is not None else None,
                    memory.embedding_model,
                    memory.revision,
                    str(memory.supersedes) if memory.supersedes else None,
                    1 if memory.training_eligible else 0,
                    memory.schema_version,
                    memory.deleted_at.isoformat() if memory.deleted_at else None,
                ))
            return memory

    async def update(self, memory: MemoryRecord, expected_revision: int | None = None) -> MemoryRecord:
        async with self._lock:
            cursor = self._conn.execute("SELECT revision FROM memories WHERE id = ?", (str(memory.id),))
            row = cursor.fetchone()
            if not row:
                raise MemoryNotFoundError(f"Memory with ID {memory.id} not found")

            current_rev = int(row["revision"])
            if expected_revision is not None and current_rev != expected_revision:
                raise MemoryConflictError(
                    f"Revision conflict for memory {memory.id}: expected {expected_revision}, got {current_rev}"
                )

            new_revision = current_rev + 1
            now = datetime.now(timezone.utc)
            memory.revision = new_revision
            memory.updated_at = now

            with self._conn:
                self._conn.execute("""
                UPDATE memories SET
                    kind = ?, sensitivity = ?, content = ?, encrypted_content = ?,
                    structured_json = ?, tags_json = ?, source_type = ?, source_ref = ?,
                    confidence = ?, importance = ?, subject = ?, predicate = ?, value_json = ?,
                    fingerprint = ?, status = ?, updated_at = ?, last_accessed_at = ?,
                    expires_at = ?, embedding_json = ?, embedding_model = ?,
                    revision = ?, supersedes = ?, training_eligible = ?,
                    schema_version = ?, deleted_at = ?
                WHERE id = ?
                """, (
                    memory.kind.value,
                    memory.sensitivity.value,
                    memory.content,
                    memory.encrypted_content,
                    json.dumps(memory.structured, ensure_ascii=False),
                    json.dumps(memory.tags, ensure_ascii=False),
                    memory.source_type.value,
                    memory.source_ref,
                    memory.confidence,
                    memory.importance,
                    memory.subject,
                    memory.predicate,
                    json.dumps(memory.value, ensure_ascii=False) if memory.value is not None else None,
                    memory.fingerprint,
                    memory.status.value,
                    memory.updated_at.isoformat(),
                    memory.last_accessed_at.isoformat() if memory.last_accessed_at else None,
                    memory.expires_at.isoformat() if memory.expires_at else None,
                    json.dumps(memory.embedding) if memory.embedding is not None else None,
                    memory.embedding_model,
                    memory.revision,
                    str(memory.supersedes) if memory.supersedes else None,
                    1 if memory.training_eligible else 0,
                    memory.schema_version,
                    memory.deleted_at.isoformat() if memory.deleted_at else None,
                    str(memory.id),
                ))
            return memory

    async def delete(self, memory_id: UUID, soft_delete: bool = True) -> bool:
        async with self._lock:
            cursor = self._conn.execute("SELECT id FROM memories WHERE id = ?", (str(memory_id),))
            if not cursor.fetchone():
                return False

            with self._conn:
                if soft_delete:
                    now = datetime.now(timezone.utc)
                    self._conn.execute("""
                    UPDATE memories SET
                        status = ?, deleted_at = ?, updated_at = ?, revision = revision + 1
                    WHERE id = ?
                    """, (MemoryStatus.DELETED.value, now.isoformat(), now.isoformat(), str(memory_id)))
                else:
                    self._conn.execute("DELETE FROM memories WHERE id = ?", (str(memory_id),))
            return True

    async def list(self, filters: MemoryFilters | None = None) -> list[MemoryRecord]:
        async with self._lock:
            query = "SELECT * FROM memories WHERE 1=1"
            params: list[Any] = []

            if filters:
                if filters.status is not None:
                    query += " AND status = ?"
                    params.append(filters.status.value)
                if filters.kind is not None:
                    query += " AND kind = ?"
                    params.append(filters.kind.value)
                if filters.sensitivity is not None:
                    query += " AND sensitivity = ?"
                    params.append(filters.sensitivity.value)
                if filters.subject is not None:
                    query += " AND LOWER(subject) = LOWER(?)"
                    params.append(filters.subject)
                if filters.predicate is not None:
                    query += " AND LOWER(predicate) = LOWER(?)"
                    params.append(filters.predicate)

            query += " ORDER BY updated_at DESC"
            if filters and filters.limit > 0:
                query += f" LIMIT {filters.limit}"

            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
            return [self._row_to_record(r) for r in rows]

    async def find_by_fingerprint(self, fingerprint: str) -> MemoryRecord | None:
        async with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM memories WHERE fingerprint = ? AND status != ? ORDER BY updated_at DESC LIMIT 1",
                (fingerprint, MemoryStatus.DELETED.value),
            )
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None

    async def semantic_search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filters: MemoryFilters | None = None,
    ) -> list[MemorySearchResult]:
        records = await self.list(filters=filters)
        results: list[MemorySearchResult] = []

        for rec in records:
            if not rec.embedding:
                continue
            sim = _cosine_similarity(query_embedding, rec.embedding)
            results.append(MemorySearchResult(
                record=rec,
                score=sim,
                semantic_similarity=sim,
            ))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]
