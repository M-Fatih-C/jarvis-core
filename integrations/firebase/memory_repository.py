"""Firestore implementation of MemoryRepository maintaining user isolation and security invariants."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any
from uuid import UUID
from core.config.settings import Settings, get_settings
from core.logging.setup import get_logger
from core.memory.local_store import _cosine_similarity
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
from integrations.firebase.client import FirestoreClientProvider
from integrations.firebase.exceptions import FirestoreUnavailableError

logger = get_logger("jarvis.firebase.memory")


class FirestoreMemoryRepository(MemoryRepository):
    """Remote repository storing user memories under /users/{uid}/memories."""

    def __init__(
        self,
        client_provider: FirestoreClientProvider | None = None,
        uid: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client_provider = client_provider or FirestoreClientProvider(settings=self._settings)
        self._uid = uid or self._settings.jarvis_uid

    def _get_collection(self) -> Any:
        client = self._client_provider.get_client()
        return client.collection("users").document(self._uid).collection("memories")

    def _record_to_doc(self, memory: MemoryRecord) -> dict[str, Any]:
        """Convert MemoryRecord to Firestore document enforcing security constraints."""
        if memory.sensitivity == MemorySensitivity.LOCAL_ONLY:
            raise ValueError("LOCAL_ONLY memory records must never be saved to Firestore")
        memory = memory.storage_copy()

        data: dict[str, Any] = {
            "id": str(memory.id),
            "kind": memory.kind.value,
            "sensitivity": memory.sensitivity.value,
            "structured": memory.structured,
            "tags": memory.tags,
            "source_type": memory.source_type.value,
            "source_ref": memory.source_ref,
            "confidence": memory.confidence,
            "importance": memory.importance,
            "subject": memory.subject,
            "predicate": memory.predicate,
            "value": memory.value,
            "fingerprint": memory.fingerprint,
            "status": memory.status.value,
            "created_at": memory.created_at.isoformat(),
            "updated_at": memory.updated_at.isoformat(),
            "last_accessed_at": memory.last_accessed_at.isoformat() if memory.last_accessed_at else None,
            "expires_at": memory.expires_at.isoformat() if memory.expires_at else None,
            "revision": memory.revision,
            "supersedes": str(memory.supersedes) if memory.supersedes else None,
            "training_eligible": memory.training_eligible,
            "schema_version": memory.schema_version,
            "deleted_at": memory.deleted_at.isoformat() if memory.deleted_at else None,
        }

        if memory.sensitivity == MemorySensitivity.PRIVATE:
            # Plaintext content, structured data, values, subjects, and cloud embedding are STRICTLY FORBIDDEN in Firestore
            data["content"] = None
            data["structured"] = {}
            data["value"] = None
            data["subject"] = "private_vault"
            data["predicate"] = "encrypted"
            data["embedding"] = None
            data["embedding_model"] = None
            if memory.encrypted_content:
                try:
                    data["encrypted"] = json.loads(memory.encrypted_content)
                except Exception:
                    data["encrypted"] = {"ciphertext": memory.encrypted_content}
        elif memory.sensitivity == MemorySensitivity.SECRET_REFERENCE:
            data["content"] = memory.content
            data["embedding"] = None
            data["embedding_model"] = None
        else:
            # NORMAL
            data["content"] = memory.content
            data["embedding"] = memory.embedding
            data["embedding_model"] = memory.embedding_model

        return data

    def _doc_to_record(self, data: dict[str, Any]) -> MemoryRecord:
        """Convert Firestore document back into MemoryRecord."""
        created_at = datetime.fromisoformat(data["created_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        updated_at = datetime.fromisoformat(data["updated_at"])
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)

        last_accessed = None
        if data.get("last_accessed_at"):
            last_accessed = datetime.fromisoformat(data["last_accessed_at"])
            if last_accessed.tzinfo is None:
                last_accessed = last_accessed.replace(tzinfo=timezone.utc)

        expires_at = None
        if data.get("expires_at"):
            expires_at = datetime.fromisoformat(data["expires_at"])
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

        deleted_at = None
        if data.get("deleted_at"):
            deleted_at = datetime.fromisoformat(data["deleted_at"])
            if deleted_at.tzinfo is None:
                deleted_at = deleted_at.replace(tzinfo=timezone.utc)

        encrypted_content = None
        if "encrypted" in data and data["encrypted"]:
            encrypted_content = json.dumps(data["encrypted"])

        return MemoryRecord(
            id=UUID(data["id"]),
            kind=MemoryKind(data["kind"]),
            sensitivity=MemorySensitivity(data["sensitivity"]),
            content=data.get("content"),
            encrypted_content=encrypted_content,
            structured=data.get("structured", {}),
            tags=data.get("tags", []),
            source_type=MemorySourceType(data.get("source_type", MemorySourceType.EXPLICIT_USER.value)),
            source_ref=data.get("source_ref"),
            confidence=float(data.get("confidence", 1.0)),
            importance=float(data.get("importance", 0.5)),
            subject=data.get("subject"),
            predicate=data.get("predicate"),
            value=data.get("value"),
            fingerprint=data.get("fingerprint", ""),
            status=MemoryStatus(data.get("status", MemoryStatus.ACTIVE.value)),
            created_at=created_at,
            updated_at=updated_at,
            last_accessed_at=last_accessed,
            expires_at=expires_at,
            embedding=data.get("embedding"),
            embedding_model=data.get("embedding_model"),
            revision=int(data.get("revision", 1)),
            supersedes=UUID(data["supersedes"]) if data.get("supersedes") else None,
            training_eligible=bool(data.get("training_eligible", False)),
            schema_version=int(data.get("schema_version", 1)),
            deleted_at=deleted_at,
        )

    async def get(self, memory_id: UUID) -> MemoryRecord | None:
        doc_ref = self._get_collection().document(str(memory_id))
        snapshot = await doc_ref.get()
        if not snapshot.exists:
            return None
        return self._doc_to_record(snapshot.to_dict())

    async def save(self, memory: MemoryRecord) -> MemoryRecord:
        doc_data = self._record_to_doc(memory)
        doc_ref = self._get_collection().document(str(memory.id))
        await doc_ref.set(doc_data)
        return memory

    async def update(self, memory: MemoryRecord, expected_revision: int | None = None) -> MemoryRecord:
        doc_ref = self._get_collection().document(str(memory.id))
        snapshot = await doc_ref.get()
        if not snapshot.exists:
            raise MemoryNotFoundError(f"Memory {memory.id} not found in Firestore")

        curr_data = snapshot.to_dict()
        curr_rev = int(curr_data.get("revision", 1))

        if expected_revision is not None and curr_rev != expected_revision:
            raise MemoryConflictError(
                f"Firestore revision conflict for memory {memory.id}: expected {expected_revision}, got {curr_rev}"
            )

        new_rev = curr_rev + 1
        now = datetime.now(timezone.utc)
        memory.revision = new_rev
        memory.updated_at = now

        doc_data = self._record_to_doc(memory)
        await doc_ref.set(doc_data)
        return memory

    async def delete(self, memory_id: UUID, soft_delete: bool = True) -> bool:
        doc_ref = self._get_collection().document(str(memory_id))
        snapshot = await doc_ref.get()
        if not snapshot.exists:
            return False

        if soft_delete:
            now = datetime.now(timezone.utc).isoformat()
            await doc_ref.update({
                "status": MemoryStatus.DELETED.value,
                "deleted_at": now,
                "updated_at": now,
                "revision": snapshot.to_dict().get("revision", 1) + 1,
            })
        else:
            await doc_ref.delete()
        return True

    async def list(self, filters: MemoryFilters | None = None) -> list[MemoryRecord]:
        col = self._get_collection()
        query = col

        if filters:
            if filters.status is not None:
                query = query.where("status", "==", filters.status.value)
            if filters.kind is not None:
                query = query.where("kind", "==", filters.kind.value)
            if filters.sensitivity is not None:
                query = query.where("sensitivity", "==", filters.sensitivity.value)
            if filters.limit > 0:
                query = query.limit(filters.limit)

        snapshots = await query.get()
        records: list[MemoryRecord] = []
        for snap in snapshots:
            records.append(self._doc_to_record(snap.to_dict()))

        # In-memory post-filtering for case-insensitive subject/predicate
        if filters:
            if filters.subject is not None:
                subj_low = filters.subject.lower()
                records = [r for r in records if r.subject and r.subject.lower() == subj_low]
            if filters.predicate is not None:
                pred_low = filters.predicate.lower()
                records = [r for r in records if r.predicate and r.predicate.lower() == pred_low]

        return records

    async def find_by_fingerprint(self, fingerprint: str) -> MemoryRecord | None:
        col = self._get_collection()
        snapshots = await col.where("fingerprint", "==", fingerprint).where("status", "!=", MemoryStatus.DELETED.value).limit(1).get()
        if not snapshots:
            return None
        return self._doc_to_record(snapshots[0].to_dict())

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
