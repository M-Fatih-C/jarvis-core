"""Milestone 5.2.1: Personal Memory Security Hardening & Integration Test Suite.

Validates:
1. Zero plaintext storage for PRIVATE records in SQLite.
2. Unified AES-256-GCM encryption with AAD binding.
3. KeyProvider lifecycle, restart persistence, and safe fail-closed on invalid key.
4. Deterministic HMAC-SHA256 fingerprinting without plaintext leakage.
5. Strict query category access control (no financial/health leakage into general queries).
6. Idempotent import behavior without duplication.
7. Proper handling of CONFLICT and REQUIRES_VERIFICATION statuses.
8. Accurate retrieval across the 10 personalization questions.
"""

from __future__ import annotations

import json
from uuid import uuid4
import pytest

from core.config.settings import Settings
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
    VerificationStatus,
)
from core.memory.retrieval import MemoryRetriever
from core.memory.service import MemoryService


@pytest.fixture
def mock_embedder() -> DeterministicMockEmbeddingProvider:
    return DeterministicMockEmbeddingProvider(dimensions=384)


@pytest.fixture
def memory_repo() -> SQLiteMemoryRepository:
    return SQLiteMemoryRepository(":memory:")


@pytest.fixture
def key_provider() -> InMemoryKeyProvider:
    return InMemoryKeyProvider()


@pytest.fixture
def memory_service(
    memory_repo: SQLiteMemoryRepository,
    mock_embedder: DeterministicMockEmbeddingProvider,
    key_provider: InMemoryKeyProvider,
) -> MemoryService:
    return MemoryService(
        repository=memory_repo,
        embedding_provider=mock_embedder,
        key_provider=key_provider,
    )


@pytest.fixture
def memory_retriever(
    memory_repo: SQLiteMemoryRepository,
    mock_embedder: DeterministicMockEmbeddingProvider,
    key_provider: InMemoryKeyProvider,
) -> MemoryRetriever:
    return MemoryRetriever(
        repository=memory_repo,
        embedding_provider=mock_embedder,
        key_provider=key_provider,
        settings=Settings(memory_top_k=5, max_memory_context_chars=3000),
    )


@pytest.mark.asyncio
async def test_private_memory_zero_plaintext_in_sqlite(
    memory_service: MemoryService,
    memory_repo: SQLiteMemoryRepository,
) -> None:
    """Verify that PRIVATE records have NO plaintext in SQLite columns."""
    sensitive_content = "Hassas kimlik no: 12345678901, Gizli Maas: 50000 TL"
    cand = MemoryCandidate(
        kind=MemoryKind.PROFILE,
        content=sensitive_content,
        sensitivity=MemorySensitivity.PRIVATE,
        confidence=1.0,
        source_id="test.sensitive.identity",
        category="identity_and_school_ids",
        source_type=MemorySourceType.EXPLICIT_USER,
        structured={"tc_no": "12345678901", "salary": 50000},
    )

    rec = await memory_service.store_candidate(cand)
    assert rec is not None
    assert rec.sensitivity == MemorySensitivity.PRIVATE

    # Direct raw row inspection on underlying SQLite connection
    cursor = memory_repo._conn.execute(
        "SELECT content, structured_json, value_json, embedding_json, subject, predicate, fingerprint, encrypted_content FROM memories WHERE id = ?",
        (str(rec.id),),
    )
    row = cursor.fetchone()
    assert row is not None

    # Plaintext must be completely absent from all queryable columns
    assert row["content"] is None or row["content"] == ""
    assert row["structured_json"] in (None, "", "{}")
    assert row["value_json"] is None
    assert row["embedding_json"] is None
    assert row["subject"] == "private_vault"
    assert row["predicate"] == "encrypted"

    # Encrypted content must be valid AES-256-GCM JSON payload
    assert row["encrypted_content"] is not None
    assert "ciphertext" in row["encrypted_content"]
    assert "nonce" in row["encrypted_content"]
    assert sensitive_content not in row["encrypted_content"]

    # Plaintext audit must report 0 leaks
    leaks = await memory_repo.audit_plaintext_private_records()
    assert len(leaks) == 0


@pytest.mark.asyncio
async def test_private_memory_crypto_aad_binding_and_wrong_key(
    key_provider: InMemoryKeyProvider,
) -> None:
    """Verify AES-256-GCM AAD binding and safe fail-closed behavior on wrong key."""
    key1 = await key_provider.get_or_create_memory_key()
    provider2 = InMemoryKeyProvider()
    key2 = await provider2.get_or_create_memory_key()

    test_id = uuid4()
    payload = {"content": "Gizli finansal borç", "structured": {"amount": 25000}}

    encrypted = MemoryEncryptor.encrypt_private_payload(
        payload,
        key1,
        record_id=test_id,
        schema_version=1,
    )

    # Decrypt with correct key and AAD succeeds
    decrypted = MemoryEncryptor.decrypt_private_payload(
        encrypted,
        key1,
        record_id=test_id,
        schema_version=1,
    )
    assert decrypted["content"] == "Gizli finansal borç"
    assert decrypted["structured"]["amount"] == 25000

    # Decrypt with wrong key raises CryptoError
    with pytest.raises(CryptoError):
        MemoryEncryptor.decrypt_private_payload(
            encrypted,
            key2,
            record_id=test_id,
            schema_version=1,
        )

    # Decrypt with wrong record_id (tampered AAD) raises CryptoError
    with pytest.raises(CryptoError):
        MemoryEncryptor.decrypt_private_payload(
            encrypted,
            key1,
            record_id=uuid4(),
            schema_version=1,
        )


@pytest.mark.asyncio
async def test_private_fingerprint_hmac_not_plaintext_predictable(
    key_provider: InMemoryKeyProvider,
) -> None:
    """Verify that private record fingerprints use HMAC-SHA256 derived key, not plain SHA256."""
    key = await key_provider.get_or_create_memory_key()
    hmac_key = MemoryEncryptor.derive_fingerprint_key(key)

    text = "Kullanıcı özel sağlık notu"
    hmac_fp = DeduplicationEngine.generate_fingerprint(
        kind="profile",
        content=text,
        hmac_key=hmac_key,
    )

    # Predictable standard SHA256 without HMAC key
    plain_fp = DeduplicationEngine.generate_fingerprint(
        kind="profile",
        content=text,
        hmac_key=None,
    )

    assert hmac_fp != plain_fp
    assert len(hmac_fp) == 64  # SHA256 hex string


@pytest.mark.asyncio
async def test_restart_persistence_and_decryption(
    memory_repo: SQLiteMemoryRepository,
    mock_embedder: DeterministicMockEmbeddingProvider,
    key_provider: InMemoryKeyProvider,
) -> None:
    """Simulate app restart: new service & retriever instances recover private memory with same key."""
    service1 = MemoryService(memory_repo, mock_embedder, key_provider=key_provider)
    cand = MemoryCandidate(
        kind=MemoryKind.PROFILE,
        content="Özel Sağlık: Menisküs yırtığı ameliyatı geçmişi",
        sensitivity=MemorySensitivity.PRIVATE,
        confidence=1.0,
        source_id="test.health.knee",
        category="health_historical",
    )
    saved = await service1.store_candidate(cand)
    assert saved is not None

    # Simulate restart: instantiate new retriever with same key provider
    retriever2 = MemoryRetriever(
        repository=memory_repo,
        embedding_provider=mock_embedder,
        key_provider=key_provider,
        settings=Settings(memory_top_k=5),
    )

    # Retrieve matching health query
    hits = await retriever2.retrieve("Diz ve menisküs sağlık durumum nedir?")
    assert len(hits) >= 1
    top = hits[0].record
    assert top.category == "health_historical"
    assert top.content is not None
    assert "Menisküs yırtığı" in top.content


@pytest.mark.asyncio
async def test_category_access_control_no_leakage(
    memory_service: MemoryService,
    memory_retriever: MemoryRetriever,
) -> None:
    """Verify that private categories (debt, medical) are NOT unlocked for unrelated general queries."""
    # Store private debt record
    await memory_service.store_candidate(MemoryCandidate(
        kind=MemoryKind.WORK,
        content="Banka Borcu: QNB Kredi kartı 45.000 TL bakiye",
        sensitivity=MemorySensitivity.PRIVATE,
        confidence=1.0,
        source_id="test.finance.debt",
        category="financial_historical",
    ))

    # Store private health record
    await memory_service.store_candidate(MemoryCandidate(
        kind=MemoryKind.PROFILE,
        content="Sağlık Notu: Düzenli D vitamini ve magnezyum kullanıyor",
        sensitivity=MemorySensitivity.PRIVATE,
        confidence=1.0,
        source_id="test.health.supplements",
        category="health_historical",
    ))

    # Store normal course schedule record
    await memory_service.store_candidate(MemoryCandidate(
        kind=MemoryKind.EDUCATION,
        content="TÜRTEP Salı 18:00 İşletme Bilimine Giriş, Çarşamba 20:00 İşletme Matematiği",
        sensitivity=MemorySensitivity.LOCAL_ONLY,
        confidence=1.0,
        source_id="test.education.schedule",
        category="education_schedule",
    ))

    # Query 1: Class schedule -> must NOT leak bank debt or medical supplements
    hits_schedule = await memory_retriever.retrieve("Bu hafta derslerim hangi saatlerde?")
    contents_schedule = [h.record.content or "" for h in hits_schedule]
    full_text_schedule = " ".join(contents_schedule)

    assert "İşletme" in full_text_schedule
    assert "Banka Borcu" not in full_text_schedule
    assert "45.000" not in full_text_schedule
    assert "D vitamini" not in full_text_schedule

    # Query 2: Financial query -> permits financial private record
    hits_fin = await memory_retriever.retrieve("Şu anki banka borcum ne kadar?")
    contents_fin = [h.record.content or "" for h in hits_fin]
    full_text_fin = " ".join(contents_fin)

    assert "Banka Borcu" in full_text_fin
    assert "D vitamini" not in full_text_fin  # Health remains closed


@pytest.mark.asyncio
async def test_conflict_and_verification_status_provenance(
    memory_service: MemoryService,
    memory_retriever: MemoryRetriever,
) -> None:
    """Verify that CONFLICT status is accurately retained and formatted in retrieved context."""
    cand = MemoryCandidate(
        kind=MemoryKind.EDUCATION,
        content="Anadolu AÖF İktisat 1. yarıyıl belgesi ile 5-8. yarıyıl ders planı çelişkisi",
        sensitivity=MemorySensitivity.LOCAL_ONLY,
        confidence=0.80,
        source_id="education.anadolu.conflict",
        category="education",
        as_of="2026-09-16",
        verification_status=VerificationStatus.CONFLICT,
    )
    rec = await memory_service.store_candidate(cand)
    assert rec is not None
    assert rec.verification_status == VerificationStatus.CONFLICT

    hits = await memory_retriever.retrieve("Anadolu'da şu an hangi sınıftayım?")
    assert len(hits) >= 1

    formatted = memory_retriever.format_context_block(hits)
    assert "status=conflict" in formatted
    assert "as_of=2026-09-16" in formatted


@pytest.mark.asyncio
async def test_import_idempotency_no_duplicates(
    memory_service: MemoryService,
    memory_repo: SQLiteMemoryRepository,
) -> None:
    """Verify idempotent storage: storing identical source_id does not duplicate records."""
    cand = MemoryCandidate(
        kind=MemoryKind.PROFILE,
        content="Kullanıcı: Fatih, Sakarya",
        sensitivity=MemorySensitivity.LOCAL_ONLY,
        confidence=1.0,
        source_id="identity.name",
        category="identity",
        as_of="2026-10-03",
    )

    rec1 = await memory_service.store_candidate(cand)
    assert rec1 is not None

    # Check find_by_source_id
    existing = await memory_repo.find_by_source_id("identity.name")
    assert existing is not None
    assert existing.id == rec1.id

    # Count records in table
    count1 = memory_repo._conn.execute("SELECT COUNT(*) FROM memories WHERE source_id = 'identity.name'").fetchone()[0]
    assert count1 == 1

    # Second store with same content returns existing deduplicated record
    rec2 = await memory_service.store_candidate(cand)
    assert rec2 is not None
    assert rec2.id == rec1.id

    count2 = memory_repo._conn.execute("SELECT COUNT(*) FROM memories WHERE source_id = 'identity.name'").fetchone()[0]
    assert count2 == 1


@pytest.mark.asyncio
async def test_personalization_10_queries_suite(
    memory_service: MemoryService,
    memory_retriever: MemoryRetriever,
) -> None:
    """Comprehensive test validating all 10 Milestone 5.2.1 personalization questions."""
    seed_items = [
        MemoryCandidate(
            kind=MemoryKind.EDUCATION,
            content="Ben hangi üniversitelerde okuyorum: Örnek Akademi TÜRTEP YBS ve Örnek Açıköğretim AÖF İktisat.",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="education",
            source_id="education.universities",
            confidence=1.0,
        ),
        MemoryCandidate(
            kind=MemoryKind.EDUCATION,
            content="TÜRTEP'te hangi derslerim var: İşletme Bilimine Giriş, Genel Muhasebe, İşletme Matematiği, Programlama.",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="education",
            source_id="education.turtep_courses",
            confidence=1.0,
        ),
        MemoryCandidate(
            kind=MemoryKind.EDUCATION,
            content="Bu hafta derslerim hangi saatlerde: Canlı Ders Saatleri Salı 18:00, Salı 21:00, Çarşamba 20:00 [Örnek Çizelgedir, portaldan teyit edilmelidir].",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="education_schedule",
            source_id="education.turtep_schedule",
            confidence=0.85,
            verification_status=VerificationStatus.HISTORICAL,
        ),
        MemoryCandidate(
            kind=MemoryKind.EDUCATION,
            content="İki üniversitemin sınavları ne zaman: Güz Vize 21-29 Kasım 2026, Bahar Vize 5-6 Aralık 2026.",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="academic_calendar",
            source_id="education.exam_calendar",
            confidence=1.0,
        ),
        MemoryCandidate(
            kind=MemoryKind.EDUCATION,
            content="Anadolu'da şu an hangi sınıftayım: Belgede 1. yarıyıl görünürken planda 5-8. yarıyıl var [DOĞRULAMA GEREKİYOR].",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="education",
            source_id="education.anadolu_status",
            confidence=0.80,
            verification_status=VerificationStatus.CONFLICT,
        ),
        MemoryCandidate(
            kind=MemoryKind.WORK,
            content="Önceki iş deneyimlerim neler: Örnek Sağlık Grubu IT Uzmanı, Alpha Robotics Otonom Sistemler Teknikeri.",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="employment",
            source_id="work.experience",
            confidence=1.0,
        ),
        MemoryCandidate(
            kind=MemoryKind.PROJECT,
            content="Hangi yazılım projelerim üzerinde çalışıyorum: JARVIS V1 (M5.2.1), Synthetic App yetenek takası, OSINT Framework, AI Business OS.",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="projects",
            source_id="projects.all",
            confidence=1.0,
        ),
        MemoryCandidate(
            kind=MemoryKind.PREFERENCE,
            content="İş ararken hangi alanlara öncelik veriyorum: Backend / Full-stack Developer, AI/ML Developer, IT Uzmanı. Saha hariç.",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="goals",
            source_id="career.job_search",
            confidence=0.90,
        ),
        MemoryCandidate(
            kind=MemoryKind.PREFERENCE,
            content="Bu akşam iki saatlik çalışma planlayabilir misin: Çalışma blokları Apple Calendar uygunluğu kontrol edilerek ve onay ile planlanır.",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="preferences",
            source_id="preferences.planning",
            confidence=0.90,
        ),
        MemoryCandidate(
            kind=MemoryKind.WORK,
            content="Şu anki banka borcum ne kadar: Tarihsel Borçlar (Ağustos 2026): Örnek Kredi Kartı ve Örnek Öğrenim Kredisi planı. [GÜNCEL BAKİYE DEĞİLDİR].",
            sensitivity=MemorySensitivity.PRIVATE,
            category="financial_historical",
            source_id="financial.debts",
            confidence=0.85,
            verification_status=VerificationStatus.HISTORICAL,
        ),
    ]

    for item in seed_items:
        await memory_service.store_candidate(item)

    test_queries = [
        ("Ben hangi üniversitelerde okuyorum?", ["TÜRTEP", "AÖF"]),
        ("TÜRTEP'te hangi derslerim var?", ["İşletme", "Muhasebe"]),
        ("Bu hafta derslerim hangi saatlerde?", ["Salı", "18:00"]),
        ("İki üniversitemin sınavları ne zaman?", ["21-29 Kasım", "5-6 Aralık"]),
        ("Anadolu'da şu an hangi sınıftayım?", ["1. yarıyıl", "5-8"]),
        ("Önceki iş deneyimlerim neler?", ["IT Uzmanı", "Otonom"]),
        ("Hangi yazılım projelerim üzerinde çalışıyorum?", ["JARVIS", "OSINT"]),
        ("İş ararken hangi alanlara öncelik veriyorum?", ["Developer", "Uzmanı"]),
        ("Bu akşam iki saatlik çalışma planlayabilir misin?", ["Apple Calendar", "onay"]),
        ("Şu anki banka borcum ne kadar?", ["Kredi Kartı", "Kredisi"]),
    ]

    for q, expected_keywords in test_queries:
        hits = await memory_retriever.retrieve(q, limit=5)
        assert len(hits) >= 1, f"Query '{q}' returned no results"
        combined_text = " ".join([h.record.content or "" for h in hits])
        for kw in expected_keywords:
            assert kw in combined_text, f"Expected '{kw}' in results for query '{q}', got: {combined_text}"
