"""FastAPI routes for memory management, search, and status."""

from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from api.dependencies import get_memory_service
from core.memory.models import (
    MemoryCandidate,
    MemoryFilters,
    MemoryKind,
    MemoryRecord,
    MemorySensitivity,
    MemoryStatus,
)
from core.memory.service import MemoryService

router = APIRouter(prefix="/v1", tags=["memory"])


class CreateMemoryRequest(BaseModel):
    content: str = Field(description="Content of the memory to save")
    kind: MemoryKind = Field(default=MemoryKind.PREFERENCE, description="Classification kind")
    sensitivity: MemorySensitivity = Field(default=MemorySensitivity.NORMAL, description="Sensitivity level")
    subject: str | None = Field(default="user", description="Entity subject")
    predicate: str | None = Field(default=None, description="Property predicate")
    value: Any | None = Field(default=None, description="Value payload")
    structured: dict[str, Any] = Field(default_factory=dict, description="Structured metadata")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class UpdateMemoryRequest(BaseModel):
    content: str | None = None
    importance: float | None = None
    confidence: float | None = None
    tags: list[str] | None = None


class MemoryResponse(BaseModel):
    id: UUID
    kind: MemoryKind
    sensitivity: MemorySensitivity
    content: str | None
    structured: dict[str, Any]
    tags: list[str]
    confidence: float
    importance: float
    subject: str | None
    predicate: str | None
    value: Any | None
    status: MemoryStatus
    created_at: str
    updated_at: str
    revision: int


def _record_to_response(rec: MemoryRecord, decrypted_content: str | None = None) -> MemoryResponse:
    # Administrative loopback routes do not carry verified identity/category consent.
    # Never turn encrypted records into a private-data bypass for these callers.
    if rec.sensitivity == MemorySensitivity.PRIVATE:
        rec = rec.storage_copy()
        decrypted_content = None
    display_content = decrypted_content or rec.content
    return MemoryResponse(
        id=rec.id,
        kind=rec.kind,
        sensitivity=rec.sensitivity,
        content=display_content,
        structured=rec.structured,
        tags=rec.tags,
        confidence=rec.confidence,
        importance=rec.importance,
        subject=rec.subject,
        predicate=rec.predicate,
        value=rec.value,
        status=rec.status,
        created_at=rec.created_at.isoformat(),
        updated_at=rec.updated_at.isoformat(),
        revision=rec.revision,
    )


@router.get("/memory/status")
async def get_memory_status(
    memory: MemoryService = Depends(get_memory_service),
) -> dict[str, Any]:
    """Retrieve operational status of the memory subsystem."""
    return {
        "memory": {
            "enabled": True,
            "local": "ready",
            "cloud": "emulator" if memory._settings.firestore_emulator_host else ("ready" if memory._settings.firebase_enabled else "disabled"),
            "embedding": "ready",
        }
    }


@router.get("/memories", response_model=list[MemoryResponse])
async def list_memories(
    kind: MemoryKind | None = None,
    sensitivity: MemorySensitivity | None = None,
    status_filter: MemoryStatus | None = Query(default=MemoryStatus.ACTIVE, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    memory: MemoryService = Depends(get_memory_service),
) -> list[MemoryResponse]:
    """List stored memory records with optional filtering."""
    filters = MemoryFilters(
        kind=kind,
        sensitivity=sensitivity,
        status=status_filter,
        limit=limit,
    )
    records = await memory.repository.list(filters)
    return [_record_to_response(rec) for rec in records if rec.sensitivity != MemorySensitivity.PRIVATE]


@router.get("/memories/search")
async def search_memories(
    q: str = Query(..., description="Query to search"),
    limit: int = Query(default=5, ge=1, le=20),
    memory: MemoryService = Depends(get_memory_service),
) -> dict[str, Any]:
    """Perform ranked semantic and multi-factor memory search."""
    results = await memory.retriever.retrieve(query=q, limit=limit)
    items = []
    for hit in results:
        items.append({
            "record": _record_to_response(hit.record),
            "score": round(hit.score, 4),
            "semantic_similarity": round(hit.semantic_similarity, 4),
        })
    return {"query": q, "results": items, "count": len(items)}


@router.get("/memories/{memory_id}", response_model=MemoryResponse)
async def get_memory(
    memory_id: UUID,
    memory: MemoryService = Depends(get_memory_service),
) -> MemoryResponse:
    """Retrieve an individual memory record by ID."""
    rec = await memory.repository.get(memory_id)
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")

    if rec.sensitivity == MemorySensitivity.PRIVATE:
        raise HTTPException(status_code=403, detail="PRIVATE records require verified identity and category consent")
    return _record_to_response(rec)


@router.post("/memories", response_model=MemoryResponse, status_code=status.HTTP_201_CREATED)
async def create_memory(
    req: CreateMemoryRequest,
    memory: MemoryService = Depends(get_memory_service),
) -> MemoryResponse:
    """Explicitly store a new memory item."""
    if req.sensitivity == MemorySensitivity.PRIVATE:
        raise HTTPException(status_code=403, detail="PRIVATE writes require verified identity and category consent")
    candidate = MemoryCandidate(
        kind=req.kind,
        content=req.content,
        structured=req.structured,
        subject=req.subject,
        predicate=req.predicate,
        value=req.value,
        sensitivity=req.sensitivity,
        confidence=req.confidence,
        importance=req.importance,
    )
    saved = await memory.store_candidate(candidate)
    if not saved:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Memory candidate was rejected by security or persistence policy",
        )
    return _record_to_response(saved)


@router.patch("/memories/{memory_id}", response_model=MemoryResponse)
async def update_memory(
    memory_id: UUID,
    req: UpdateMemoryRequest,
    memory: MemoryService = Depends(get_memory_service),
) -> MemoryResponse:
    """Update an existing memory item."""
    rec = await memory.repository.get(memory_id)
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")

    if rec.sensitivity == MemorySensitivity.PRIVATE:
        raise HTTPException(status_code=403, detail="PRIVATE writes require verified identity and category consent")
    if req.content is not None:
        rec.content = req.content
        rec.embedding = await memory._embeddings.embed_document(req.content)

    if req.importance is not None:
        rec.importance = req.importance
    if req.confidence is not None:
        rec.confidence = req.confidence
    if req.tags is not None:
        rec.tags = req.tags

    updated = await memory.repository.update(rec)
    return _record_to_response(updated)


@router.delete("/memories/{memory_id}", status_code=status.HTTP_200_OK)
async def delete_memory(
    memory_id: UUID,
    memory: MemoryService = Depends(get_memory_service),
) -> dict[str, Any]:
    """Soft-delete a memory record (tombstone)."""
    rec = await memory.repository.get(memory_id)
    if rec and rec.sensitivity == MemorySensitivity.PRIVATE:
        raise HTTPException(status_code=403, detail="PRIVATE writes require verified identity and category consent")
    deleted = await memory.repository.delete(memory_id, soft_delete=True)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
    return {"status": "deleted", "id": str(memory_id)}
