"""Domain models, enums, and schemas for Jarvis Memory & Personalization."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4
from pydantic import BaseModel, ConfigDict, Field


class MemoryKind(str, Enum):
    """Categorical classification of memory items."""
    PROFILE = "profile"
    PREFERENCE = "preference"
    PERSON = "person"
    EDUCATION = "education"
    WORK = "work"
    PROJECT = "project"
    ROUTINE = "routine"
    DECISION = "decision"
    EPISODIC = "episodic"
    DOCUMENT = "document"
    AGENT_LEARNING = "agent_learning"


class MemorySensitivity(str, Enum):
    """Security classification determining sync, encryption, and embedding boundaries."""
    NORMAL = "normal"               # Syncs to Firestore in plaintext; cloud vector search enabled
    PRIVATE = "private"             # Encrypted client-side with AES-256-GCM before cloud sync; local embedding only
    LOCAL_ONLY = "local_only"       # Never synced to Firestore; stays in local SQLite only
    SECRET_REFERENCE = "secret_reference"  # Logical reference only (e.g. 'github_main'); never raw secret; no embeddings


class MemorySourceType(str, Enum):
    """Origin of the memory information."""
    EXPLICIT_USER = "explicit_user"
    INFERRED = "inferred"
    EMAIL = "email"
    CALENDAR = "calendar"
    DOCUMENT = "document"
    SYSTEM = "system"
    FEEDBACK = "feedback"


class MemoryStatus(str, Enum):
    """Lifecycle status of a memory record."""
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"
    EXPIRED = "expired"


class FeedbackType(str, Enum):
    """Type of user feedback for personalization."""
    ACCEPTED = "accepted"
    EDITED = "edited"
    REJECTED = "rejected"
    IGNORED = "ignored"
    SNOOZED = "snoozed"
    COMPLETED = "completed"


class MemoryRecord(BaseModel):
    """Persistent representation of a learned or stored memory item."""
    model_config = ConfigDict(frozen=False)

    id: UUID = Field(default_factory=uuid4)
    kind: MemoryKind
    sensitivity: MemorySensitivity
    content: str | None = None
    encrypted_content: str | None = None  # JSON string containing ciphertext, nonce, algorithm, key_version
    structured: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    source_type: MemorySourceType = MemorySourceType.EXPLICIT_USER
    source_ref: str | None = None
    confidence: float = 1.0
    importance: float = 0.5
    subject: str | None = None
    predicate: str | None = None
    value: Any | None = None
    fingerprint: str = ""
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed_at: datetime | None = None
    expires_at: datetime | None = None
    embedding: list[float] | None = None
    embedding_model: str | None = None
    revision: int = 1
    supersedes: UUID | None = None
    training_eligible: bool = False
    schema_version: int = 1
    deleted_at: datetime | None = None


class MemoryCandidate(BaseModel):
    """Extracted memory proposal before policy evaluation and persistence."""
    kind: MemoryKind
    content: str
    structured: dict[str, Any] = Field(default_factory=dict)
    subject: str | None = None
    predicate: str | None = None
    value: Any | None = None
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    confidence: float = 0.9
    importance: float = 0.5
    source_type: MemorySourceType = MemorySourceType.EXPLICIT_USER
    durable: bool = True
    training_eligible: bool = False


class MemoryFilters(BaseModel):
    """Query filters for memory retrieval."""
    kind: MemoryKind | None = None
    sensitivity: MemorySensitivity | None = None
    status: MemoryStatus | None = MemoryStatus.ACTIVE
    subject: str | None = None
    predicate: str | None = None
    tags: list[str] | None = None
    limit: int = 10


class MemorySearchResult(BaseModel):
    """Search hit containing a memory record and computed relevance scores."""
    record: MemoryRecord
    score: float
    semantic_similarity: float = 0.0


class FeedbackRecord(BaseModel):
    """User interaction feedback for training eligibility and tuning."""
    id: UUID = Field(default_factory=uuid4)
    run_id: UUID | None = None
    reference_id: str | None = None
    feedback_type: FeedbackType
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)
    training_eligible: bool = False
