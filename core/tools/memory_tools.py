"""Agent memory tools for searching, listing, remembering, updating, and forgetting memories."""

from typing import Any
from uuid import UUID
from pydantic import BaseModel, Field
from core.memory.models import (
    MemoryCandidate,
    MemoryFilters,
    MemoryKind,
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
)
from core.memory.service import MemoryService
from core.models.tools import RiskLevel, ToolDefinition, ToolResult
from core.tools.base import JarvisTool


class MemorySearchArgs(BaseModel):
    query: str = Field(description="Search query to match against memories")
    limit: int = Field(default=5, description="Maximum number of memory results to return")


class MemorySearchTool(JarvisTool):
    """Tool for searching user memories with semantic ranking."""

    definition = ToolDefinition(
        name="memory.search",
        description="Search stored user memories, preferences, and facts using semantic similarity.",
        risk_level=RiskLevel.R0_READ,
        input_schema=MemorySearchArgs.model_json_schema(),
        requires_approval=False,
    )
    args_schema = MemorySearchArgs

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory = memory_service

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        args = self.validate_arguments(arguments)
        assert isinstance(args, MemorySearchArgs)
        try:
            results = await self._memory.retriever.retrieve(query=args.query, limit=args.limit)
            items = [
                {
                    "id": str(r.record.id),
                    "kind": r.record.kind.value,
                    "content": r.record.content,
                    "score": round(r.score, 3),
                    "confidence": r.record.confidence,
                }
                for r in results
            ]
            return ToolResult(tool_call_id="", success=True, data={"matches": items, "count": len(items)})
        except Exception as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class MemoryListArgs(BaseModel):
    kind: str | None = Field(default=None, description="Optional filter by memory kind (e.g. preference, work, education)")
    limit: int = Field(default=10, description="Maximum records to return")


class MemoryListTool(JarvisTool):
    """Tool for listing active memories."""

    definition = ToolDefinition(
        name="memory.list",
        description="List active user memories and preferences.",
        risk_level=RiskLevel.R0_READ,
        input_schema=MemoryListArgs.model_json_schema(),
        requires_approval=False,
    )
    args_schema = MemoryListArgs

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory = memory_service

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        args = self.validate_arguments(arguments)
        assert isinstance(args, MemoryListArgs)
        try:
            kind_enum = MemoryKind(args.kind) if args.kind else None
            filters = MemoryFilters(kind=kind_enum, status=MemoryStatus.ACTIVE, limit=args.limit)
            records = await self._memory.repository.list(filters)
            items = [
                {
                    "id": str(r.id),
                    "kind": r.kind.value,
                    "content": r.content,
                    "updated_at": r.updated_at.isoformat(),
                }
                for r in records
            ]
            return ToolResult(tool_call_id="", success=True, data={"memories": items, "count": len(items)})
        except Exception as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class MemoryRememberArgs(BaseModel):
    content: str = Field(description="The memory content to remember")
    kind: str = Field(default="preference", description="Memory kind (e.g. preference, profile, work, routine)")
    subject: str = Field(default="user", description="Entity the memory pertains to")
    sensitivity: str = Field(default="normal", description="Sensitivity: normal or private")


class MemoryRememberTool(JarvisTool):
    """Tool to explicitly store a memory item."""

    definition = ToolDefinition(
        name="memory.remember",
        description="Explicitly store a new durable fact, preference, or routine about the user.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=MemoryRememberArgs.model_json_schema(),
        requires_approval=True,
    )
    args_schema = MemoryRememberArgs

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory = memory_service

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        args = self.validate_arguments(arguments)
        assert isinstance(args, MemoryRememberArgs)
        try:
            cand = MemoryCandidate(
                kind=MemoryKind(args.kind),
                content=args.content,
                subject=args.subject,
                sensitivity=MemorySensitivity(args.sensitivity),
                confidence=1.0,
                importance=0.8,
                source_type=MemorySourceType.EXPLICIT_USER,
                durable=True,
            )
            stored = await self._memory.store_candidate(cand)
            if stored:
                return ToolResult(
                    tool_call_id="",
                    success=True,
                    data={"id": str(stored.id), "status": stored.status.value, "content": stored.content},
                )
            return ToolResult(tool_call_id="", success=False, error="Policy rejected candidate storage")
        except Exception as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class MemoryUpdateArgs(BaseModel):
    memory_id: str = Field(description="UUID of the memory record to update")
    content: str = Field(description="New content for the memory")


class MemoryUpdateTool(JarvisTool):
    """Tool to update an existing memory item."""

    definition = ToolDefinition(
        name="memory.update",
        description="Update an existing user memory record.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=MemoryUpdateArgs.model_json_schema(),
        requires_approval=True,
    )
    args_schema = MemoryUpdateArgs

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory = memory_service

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        args = self.validate_arguments(arguments)
        assert isinstance(args, MemoryUpdateArgs)
        try:
            rec_id = UUID(args.memory_id)
            existing = await self._memory.repository.get(rec_id)
            if not existing:
                return ToolResult(tool_call_id="", success=False, error=f"Memory {rec_id} not found")

            existing.content = args.content
            # Re-generate embedding
            existing.embedding = await self._memory._embeddings.embed_document(args.content)
            updated = await self._memory.repository.update(existing)
            return ToolResult(
                tool_call_id="",
                success=True,
                data={"id": str(updated.id), "revision": updated.revision, "content": updated.content},
            )
        except Exception as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class MemoryForgetArgs(BaseModel):
    memory_id: str = Field(description="UUID of the memory record to delete or forget")


class MemoryForgetTool(JarvisTool):
    """Tool to soft-delete/forget a memory item."""

    definition = ToolDefinition(
        name="memory.forget",
        description="Permanently forget or remove a user memory item.",
        risk_level=RiskLevel.R4_DESTRUCTIVE,
        input_schema=MemoryForgetArgs.model_json_schema(),
        requires_approval=True,
    )
    args_schema = MemoryForgetArgs

    def __init__(self, memory_service: MemoryService) -> None:
        self._memory = memory_service

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        args = self.validate_arguments(arguments)
        assert isinstance(args, MemoryForgetArgs)
        try:
            rec_id = UUID(args.memory_id)
            deleted = await self._memory.repository.delete(rec_id, soft_delete=True)
            if deleted:
                return ToolResult(tool_call_id="", success=True, data={"id": str(rec_id), "status": "deleted"})
            return ToolResult(tool_call_id="", success=False, error=f"Memory {rec_id} not found")
        except Exception as exc:
            return ToolResult(tool_call_id="", success=False, error=str(exc))
