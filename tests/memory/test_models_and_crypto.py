"""Tests for memory models, crypto, policy, and SQLite repository."""

import pytest
from core.memory.crypto import CryptoError, InMemoryKeyProvider, MemoryEncryptor
from core.memory.deduplication import DeduplicationEngine
from core.memory.embeddings.mock import DeterministicMockEmbeddingProvider
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.models import (
    MemoryCandidate,
    MemoryFilters,
    MemoryKind,
    MemoryRecord,
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
)
from core.memory.policy import MemoryPolicy, SecretFilter
from core.memory.repository import MemoryConflictError


@pytest.mark.asyncio
async def test_aes_gcm_encryption_roundtrip() -> None:
    provider = InMemoryKeyProvider()
    key = await provider.get_or_create_memory_key()

    plaintext = "Gizli proje kod adı: Project Antigravity"
    encrypted = MemoryEncryptor.encrypt(plaintext, key)

    assert encrypted["algorithm"] == "AES-256-GCM"
    assert "ciphertext" in encrypted
    assert "nonce" in encrypted
    assert encrypted["ciphertext"] != plaintext

    decrypted = MemoryEncryptor.decrypt(encrypted, key)
    assert decrypted == plaintext


@pytest.mark.asyncio
async def test_aes_gcm_wrong_key_fails() -> None:
    provider1 = InMemoryKeyProvider()
    provider2 = InMemoryKeyProvider()
    key1 = await provider1.get_or_create_memory_key()
    key2 = await provider2.get_or_create_memory_key()

    encrypted = MemoryEncryptor.encrypt("Gizli veri", key1)
    with pytest.raises(CryptoError):
        MemoryEncryptor.decrypt(encrypted, key2)


def test_deterministic_secret_filter() -> None:
    # Text with OpenAI key and credit card
    raw_text = "API anahtarım sk-1234567890123456789012345678 ve kartım 4111 2222 3333 4444"
    structured = {"client_secret": "super_secret_val", "user": "fatih"}

    res = SecretFilter.filter(raw_text, structured)
    assert res.has_secret is True
    assert "sk-" not in res.redacted_content
    assert "4111" not in res.redacted_content
    assert "[REDACTED_SECRET:" in res.redacted_content
    assert res.redacted_structured["client_secret"] == "[REDACTED_SENSITIVE_FIELD]"
    assert res.redacted_structured["user"] == "fatih"


def test_memory_policy_evaluation() -> None:
    policy = MemoryPolicy()

    # Explicit normal preference
    c1 = MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        content="Hafta içi yan projelerle 18:00'den sonra ilgilenmek istiyorum.",
        subject="user",
        predicate="side_project_work_time",
        value="after_18_weekdays",
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.98,
        source_type=MemorySourceType.EXPLICIT_USER,
    )
    dec1 = policy.evaluate_candidate(c1)
    assert dec1.accepted is True
    assert dec1.initial_status == MemoryStatus.ACTIVE
    assert dec1.final_sensitivity == MemorySensitivity.NORMAL

    # Candidate with password
    c2 = MemoryCandidate(
        kind=MemoryKind.PROFILE,
        content="Benim şifrem: MySecretPass123!",
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.95,
        source_type=MemorySourceType.EXPLICIT_USER,
    )
    dec2 = policy.evaluate_candidate(c2)
    assert dec2.accepted is True
    assert dec2.final_sensitivity == MemorySensitivity.SECRET_REFERENCE
    assert "MySecretPass123!" not in dec2.cleaned_content

    # Inferred candidate
    c3 = MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        content="Kullanıcı kahve seviyor olabilir.",
        sensitivity=MemorySensitivity.NORMAL,
        confidence=0.7,
        source_type=MemorySourceType.INFERRED,
    )
    dec3 = policy.evaluate_candidate(c3)
    assert dec3.initial_status == MemoryStatus.CANDIDATE


def test_deduplication_and_contradiction() -> None:
    fp1 = DeduplicationEngine.generate_fingerprint(
        kind="preference",
        content="User prefers side projects after 18:00",
        subject="user",
        predicate="side_project_work_time",
    )
    fp2 = DeduplicationEngine.generate_fingerprint(
        kind="preference",
        content="  user prefers side  projects after 18:00  ",
        subject="User",
        predicate="side_project_work_time",
    )
    assert fp1 == fp2

    # Contradiction test
    rec = MemoryRecord(
        kind=MemoryKind.PREFERENCE,
        sensitivity=MemorySensitivity.NORMAL,
        content="User prefers side projects after 18:00",
        subject="user",
        predicate="side_project_work_time",
        value="after_18",
        status=MemoryStatus.ACTIVE,
    )

    new_cand = MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        content="User prefers side projects after 20:00",
        subject="user",
        predicate="side_project_work_time",
        value="after_20",
        source_type=MemorySourceType.EXPLICIT_USER,
    )

    contradictions = DeduplicationEngine.find_contradictions(new_cand, [rec])
    assert len(contradictions) == 1
    assert contradictions[0].id == rec.id


@pytest.mark.asyncio
async def test_sqlite_memory_repository_crud_and_conflict() -> None:
    repo = SQLiteMemoryRepository(":memory:")
    mock_embed = DeterministicMockEmbeddingProvider(dimensions=384)

    emb = await mock_embed.embed_document("Python programming language")
    rec = MemoryRecord(
        kind=MemoryKind.WORK,
        sensitivity=MemorySensitivity.NORMAL,
        content="Software engineer focusing on Python",
        subject="user",
        predicate="role",
        value="software_engineer",
        fingerprint="fp_123",
        embedding=emb,
    )

    # Save
    saved = await repo.save(rec)
    assert saved.id == rec.id

    # Get
    fetched = await repo.get(saved.id)
    assert fetched is not None
    assert fetched.content == "Software engineer focusing on Python"
    assert fetched.revision == 1

    # Update with expected revision
    fetched.content = "Senior software engineer focusing on Python"
    updated = await repo.update(fetched, expected_revision=1)
    assert updated.revision == 2

    # Revision conflict
    with pytest.raises(MemoryConflictError):
        await repo.update(updated, expected_revision=1)

    # Semantic search
    query_emb = await mock_embed.embed_query("Python developer")
    hits = await repo.semantic_search(query_emb, limit=5)
    assert len(hits) == 1
    assert hits[0].record.id == rec.id
    assert hits[0].semantic_similarity > 0.0

    # Soft Delete
    deleted = await repo.delete(saved.id, soft_delete=True)
    assert deleted is True
    # Still accessible directly or via status filter
    post_del = await repo.get(saved.id)
    assert post_del.status == MemoryStatus.DELETED
