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
from core.memory.consent import ConsentStore
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
        self._consent_store = ConsentStore(db_path=self._db_path, conn=self._conn)

    def get_consent_store(self) -> ConsentStore:
        """Return the persistent consent store for user category consent decisions."""
        return self._consent_store

    def _get_connection(self) -> sqlite3.Connection:
        return self._conn


    def _init_db(self) -> None:
        """Create table and indexes if they do not exist, and migrate missing columns."""
        with self._conn:
            self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                source_id TEXT,
                category TEXT,
                kind TEXT NOT NULL,
                sensitivity TEXT NOT NULL,
                content TEXT,
                encrypted_content TEXT,
                structured_json TEXT NOT NULL DEFAULT '{}',
                tags_json TEXT NOT NULL DEFAULT '[]',
                source_type TEXT NOT NULL,
                source_ref TEXT,
                as_of TEXT,
                valid_from TEXT,
                valid_until TEXT,
                confidence REAL NOT NULL,
                importance REAL NOT NULL,
                verification_status TEXT NOT NULL DEFAULT 'user_reported',
                subject TEXT,
                predicate TEXT,
                value_json TEXT,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                imported_at TEXT,
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

            # Safe migration for existing databases
            existing_cols = {col[1] for col in self._conn.execute("PRAGMA table_info(memories);").fetchall()}
            if "source_id" not in existing_cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN source_id TEXT;")
            if "category" not in existing_cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN category TEXT;")
            if "as_of" not in existing_cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN as_of TEXT;")
            if "valid_from" not in existing_cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN valid_from TEXT;")
            if "valid_until" not in existing_cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN valid_until TEXT;")
            if "verification_status" not in existing_cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'user_reported';")
            if "imported_at" not in existing_cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN imported_at TEXT;")

            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_status_kind ON memories (status, kind);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_fingerprint ON memories (fingerprint);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_source_id ON memories (source_id);")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_category ON memories (category);")
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

        valid_from = None
        if "valid_from" in row.keys() and row["valid_from"]:
            valid_from = datetime.fromisoformat(row["valid_from"])
            if valid_from.tzinfo is None:
                valid_from = valid_from.replace(tzinfo=timezone.utc)

        valid_until = None
        if "valid_until" in row.keys() and row["valid_until"]:
            valid_until = datetime.fromisoformat(row["valid_until"])
            if valid_until.tzinfo is None:
                valid_until = valid_until.replace(tzinfo=timezone.utc)

        imported_at = None
        if "imported_at" in row.keys() and row["imported_at"]:
            imported_at = datetime.fromisoformat(row["imported_at"])
            if imported_at.tzinfo is None:
                imported_at = imported_at.replace(tzinfo=timezone.utc)

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

        from core.memory.models import VerificationStatus
        v_status_str = row["verification_status"] if "verification_status" in row.keys() and row["verification_status"] else "user_reported"
        try:
            verif_status = VerificationStatus(v_status_str)
        except Exception:
            verif_status = VerificationStatus.USER_REPORTED

        return MemoryRecord(
            id=UUID(row["id"]),
            source_id=row["source_id"] if "source_id" in row.keys() else None,
            category=row["category"] if "category" in row.keys() else None,
            kind=MemoryKind(row["kind"]),
            sensitivity=MemorySensitivity(row["sensitivity"]),
            content=row["content"],
            encrypted_content=row["encrypted_content"],
            structured=json.loads(row["structured_json"]) if row["structured_json"] else {},
            tags=json.loads(row["tags_json"]) if row["tags_json"] else [],
            source_type=MemorySourceType(row["source_type"]),
            source_ref=row["source_ref"],
            as_of=row["as_of"] if "as_of" in row.keys() else None,
            valid_from=valid_from,
            valid_until=valid_until,
            confidence=float(row["confidence"]),
            importance=float(row["importance"]),
            verification_status=verif_status,
            subject=row["subject"],
            predicate=row["predicate"],
            value=value,
            fingerprint=row["fingerprint"],
            status=MemoryStatus(row["status"]),
            created_at=created_at,
            updated_at=updated_at,
            imported_at=imported_at,
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
        memory = memory.storage_copy()
        async with self._lock:
            with self._conn:
                self._conn.execute("""
                INSERT INTO memories (
                    id, source_id, category, kind, sensitivity, content, encrypted_content,
                    structured_json, tags_json, source_type, source_ref, as_of, valid_from,
                    valid_until, confidence, importance, verification_status, subject, predicate,
                    value_json, fingerprint, status, created_at, updated_at, imported_at,
                    last_accessed_at, expires_at, embedding_json, embedding_model, revision,
                    supersedes, training_eligible, schema_version, deleted_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """, (
                    str(memory.id),
                    memory.source_id,
                    memory.category,
                    memory.kind.value,
                    memory.sensitivity.value,
                    memory.content,
                    memory.encrypted_content,
                    json.dumps(memory.structured, ensure_ascii=False),
                    json.dumps(memory.tags, ensure_ascii=False),
                    memory.source_type.value,
                    memory.source_ref,
                    memory.as_of,
                    memory.valid_from.isoformat() if memory.valid_from else None,
                    memory.valid_until.isoformat() if memory.valid_until else None,
                    memory.confidence,
                    memory.importance,
                    memory.verification_status.value,
                    memory.subject,
                    memory.predicate,
                    json.dumps(memory.value, ensure_ascii=False) if memory.value is not None else None,
                    memory.fingerprint,
                    memory.status.value,
                    memory.created_at.isoformat(),
                    memory.updated_at.isoformat(),
                    memory.imported_at.isoformat() if memory.imported_at else None,
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
        memory = memory.storage_copy()
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
                    source_id = ?, category = ?, kind = ?, sensitivity = ?, content = ?,
                    encrypted_content = ?, structured_json = ?, tags_json = ?, source_type = ?,
                    source_ref = ?, as_of = ?, valid_from = ?, valid_until = ?, confidence = ?,
                    importance = ?, verification_status = ?, subject = ?, predicate = ?,
                    value_json = ?, fingerprint = ?, status = ?, updated_at = ?, imported_at = ?,
                    last_accessed_at = ?, expires_at = ?, embedding_json = ?, embedding_model = ?,
                    revision = ?, supersedes = ?, training_eligible = ?, schema_version = ?,
                    deleted_at = ?
                WHERE id = ?
                """, (
                    memory.source_id,
                    memory.category,
                    memory.kind.value,
                    memory.sensitivity.value,
                    memory.content,
                    memory.encrypted_content,
                    json.dumps(memory.structured, ensure_ascii=False),
                    json.dumps(memory.tags, ensure_ascii=False),
                    memory.source_type.value,
                    memory.source_ref,
                    memory.as_of,
                    memory.valid_from.isoformat() if memory.valid_from else None,
                    memory.valid_until.isoformat() if memory.valid_until else None,
                    memory.confidence,
                    memory.importance,
                    memory.verification_status.value,
                    memory.subject,
                    memory.predicate,
                    json.dumps(memory.value, ensure_ascii=False) if memory.value is not None else None,
                    memory.fingerprint,
                    memory.status.value,
                    memory.updated_at.isoformat(),
                    memory.imported_at.isoformat() if memory.imported_at else None,
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
                if filters.source_id is not None:
                    query += " AND source_id = ?"
                    params.append(filters.source_id)
                if filters.category is not None:
                    query += " AND category = ?"
                    params.append(filters.category)
                if filters.verification_status is not None:
                    query += " AND verification_status = ?"
                    params.append(filters.verification_status.value)
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

    async def find_by_source_id(self, source_id: str) -> MemoryRecord | None:
        async with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM memories WHERE source_id = ? AND status != ? ORDER BY updated_at DESC LIMIT 1",
                (source_id, MemoryStatus.DELETED.value),
            )
            row = cursor.fetchone()
            return self._row_to_record(row) if row else None

    async def audit_plaintext_private_records(self) -> list[dict[str, Any]]:
        """Audit the database for any PRIVATE records that leak plaintext content or structured data.
        
        Returns metadata summaries without displaying sensitive payload contents.
        """
        async with self._lock:
            cursor = self._conn.execute("""
            SELECT id, kind, source_id, category,
                   (content IS NOT NULL AND content != '') AS has_content,
                   (structured_json IS NOT NULL AND structured_json != '{}' AND structured_json != '') AS has_structured,
                   (value_json IS NOT NULL AND value_json != '') AS has_value,
                   (embedding_json IS NOT NULL AND embedding_json != '') AS has_embedding
            FROM memories
            WHERE sensitivity = 'private'
              AND (
                  (content IS NOT NULL AND content != '')
                  OR (structured_json IS NOT NULL AND structured_json != '{}' AND structured_json != '')
                  OR (value_json IS NOT NULL AND value_json != '')
                  OR (embedding_json IS NOT NULL AND embedding_json != '')
              )
            """)
            leaks = []
            for r in cursor.fetchall():
                leaked_fields = []
                if r["has_content"]:
                    leaked_fields.append("content")
                if r["has_structured"]:
                    leaked_fields.append("structured_json")
                if r["has_value"]:
                    leaked_fields.append("value_json")
                if r["has_embedding"]:
                    leaked_fields.append("embedding_json")
                leaks.append({
                    "id": r["id"],
                    "kind": r["kind"],
                    "source_id": r["source_id"],
                    "category": r["category"],
                    "leaked_fields": leaked_fields,
                })
            return leaks

    def audit_storage_guarantees(self) -> dict[str, Any]:
        """Audit storage guarantees distinguishing between PRIVATE, LOCAL_ONLY, NORMAL, and SECRET_REFERENCE."""
        with self._conn:
            cursor = self._conn.cursor()
            cursor.execute("SELECT sensitivity, count(*) FROM memories GROUP BY sensitivity")
            counts = {r[0]: r[1] for r in cursor.fetchall()}

            # Check PRIVATE records for plaintext leaks
            cursor.execute("""
                SELECT count(*) FROM memories
                WHERE sensitivity = 'private'
                  AND (
                      (content IS NOT NULL AND content != '')
                      OR (embedding_json IS NOT NULL AND embedding_json != '')
                      OR (structured_json IS NOT NULL AND structured_json NOT IN ('', '{}'))
                      OR (value_json IS NOT NULL AND value_json != '')
                      OR (subject IS NOT NULL AND subject != 'private_vault')
                      OR (predicate IS NOT NULL AND predicate != 'encrypted')
                      OR (tags_json IS NOT NULL AND tags_json NOT IN ('', '[]'))
                      OR (source_ref IS NOT NULL AND source_ref != '')
                      OR encrypted_content IS NULL OR encrypted_content = ''
                  )
            """)
            private_leaks = cursor.fetchone()[0]

            return {
                "storage_model": "record_payload_encryption",
                "database_encryption_scope": "Individual PRIVATE payloads are encrypted with AES-256-GCM via macOS Keychain; the SQLite container is standard local storage.",
                "sensitivity_counts": {
                    "private": counts.get("private", 0),
                    "local_only": counts.get("local_only", 0),
                    "normal": counts.get("normal", 0),
                    "secret_reference": counts.get("secret_reference", 0),
                },
                "guarantees": {
                    "private_encrypted_locally": counts.get("private", 0) > 0 and private_leaks == 0,
                    "private_plaintext_leaks": private_leaks,
                },
            }

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
