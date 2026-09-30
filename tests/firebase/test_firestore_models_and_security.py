"""Tests for Firestore data mapping, security invariants, and security rules."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import pytest
from uuid import uuid4

from core.config.settings import Settings
from core.memory.crypto import InMemoryKeyProvider, MemoryEncryptor
from core.memory.models import (
    MemoryKind,
    MemoryRecord,
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
)
from integrations.firebase.memory_repository import FirestoreMemoryRepository


@pytest.fixture
def firestore_repo() -> FirestoreMemoryRepository:
    settings = Settings(firebase_project_id="test-project", jarvis_uid="user_abc123")
    return FirestoreMemoryRepository(client_provider=None, uid="user_abc123", settings=settings)


def test_normal_memory_firestore_mapping(firestore_repo: FirestoreMemoryRepository) -> None:
    now = datetime.now(timezone.utc)
    rec = MemoryRecord(
        id=uuid4(),
        kind=MemoryKind.PREFERENCE,
        sensitivity=MemorySensitivity.NORMAL,
        content="User prefers working after 18:00",
        structured={"after": "18:00"},
        confidence=0.95,
        importance=0.8,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp1",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        embedding=[0.1, 0.2, 0.3],
        embedding_model="test-embedder",
    )
    doc = firestore_repo._record_to_doc(rec)

    assert doc["kind"] == "preference"
    assert doc["sensitivity"] == "normal"
    assert doc["content"] == "User prefers working after 18:00"
    assert doc["embedding"] == [0.1, 0.2, 0.3]
    assert doc["status"] == "active"
    assert "encrypted" not in doc


def test_private_memory_firestore_mapping_strips_plaintext_and_embedding(
    firestore_repo: FirestoreMemoryRepository,
) -> None:
    now = datetime.now(timezone.utc)
    enc = {"ciphertext": "c2VjcmV0...", "nonce": "bm9uY2U...", "algorithm": "AES-256-GCM", "key_version": 1}
    rec = MemoryRecord(
        id=uuid4(),
        kind=MemoryKind.PROFILE,
        sensitivity=MemorySensitivity.PRIVATE,
        content=None,
        encrypted_content=json.dumps(enc),
        structured={"confidential": True},
        confidence=1.0,
        importance=0.9,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp_private",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        embedding=None,
    )
    doc = firestore_repo._record_to_doc(rec)

    # Security invariant 5: PRIVATE never exists as plaintext in Firestore
    assert doc["content"] is None
    # Security invariant 6: PRIVATE cloud embeddings are forbidden
    assert doc["embedding"] is None
    assert doc["embedding_model"] is None
    assert doc["encrypted"]["ciphertext"] == "c2VjcmV0..."


def test_local_only_memory_rejected_from_firestore(firestore_repo: FirestoreMemoryRepository) -> None:
    now = datetime.now(timezone.utc)
    rec = MemoryRecord(
        id=uuid4(),
        kind=MemoryKind.PROJECT,
        sensitivity=MemorySensitivity.LOCAL_ONLY,
        content="This should never reach the cloud",
        structured={},
        confidence=1.0,
        importance=0.9,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp_local",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )

    # Security invariant 4: LOCAL_ONLY never syncs to cloud
    with pytest.raises(ValueError, match="LOCAL_ONLY memory records must never be saved to Firestore"):
        firestore_repo._record_to_doc(rec)


def test_secret_reference_memory_has_no_embedding(firestore_repo: FirestoreMemoryRepository) -> None:
    now = datetime.now(timezone.utc)
    rec = MemoryRecord(
        id=uuid4(),
        kind=MemoryKind.PROFILE,
        sensitivity=MemorySensitivity.SECRET_REFERENCE,
        content="github_main",
        structured={"service": "github"},
        confidence=1.0,
        importance=0.9,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp_sec",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        embedding=None,
    )
    doc = firestore_repo._record_to_doc(rec)
    assert doc["content"] == "github_main"
    assert doc["embedding"] is None


def test_firestore_security_rules_file_exists_and_enforces_user_isolation() -> None:
    rules_path = Path("cloud/firestore.rules")
    assert rules_path.exists(), "firestore.rules file must exist"
    content = rules_path.read_text(encoding="utf-8")

    # Verify user-level isolation rules
    assert "match /users/{userId}" in content
    assert "request.auth != null" in content
    assert "request.auth.uid == userId" in content
