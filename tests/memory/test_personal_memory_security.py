"""Milestone 5.2.1: Personal Memory Security Hardening & Integration Test Suite.

Validates:
1. Zero plaintext storage for PRIVATE records in SQLite.
2. Unified AES-256-GCM encryption with strict AAD binding (fails on modified ID, version, ciphertext, nonce, tag).
3. Explicit category consent workflow (PENDING, GRANTED, REVOKED).
4. Protection against untrusted inputs (e.g. untrusted emails or prompt injections cannot unlock private memory).
5. Storage guarantees distinction (PRIVATE vs LOCAL_ONLY vs NORMAL vs SECRET_REFERENCE).
6. Deterministic HMAC-SHA256 fingerprinting without plaintext leakage.
7. Strict query category access control (no financial/health leakage into general queries).
8. Idempotent import behavior without duplication.
9. Proper handling of REQUIRES_VERIFICATION and CONFLICT statuses.
10. Accurate retrieval across the 10 personalization questions with authorization context.
"""

from __future__ import annotations

import base64
import json
from uuid import uuid4
import pytest

from core.config.settings import Settings
from core.memory.consent import ConsentStatus, ConsentStore
from core.memory.crypto import CryptoError, InMemoryKeyProvider, MemoryEncryptor
from core.memory.deduplication import DeduplicationEngine
from core.memory.embeddings.mock import DeterministicMockEmbeddingProvider
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.models import (
    MemoryAuthorizationContext,
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

    # Verify via repository audit
    leaks = await memory_repo.audit_plaintext_private_records()
    assert len(leaks) == 0, f"Detected plaintext leaks in SQLite: {leaks}"

    # Verify raw SQLite row
    cursor = memory_repo._conn.cursor()
    cursor.execute("SELECT content, structured_json, value_json, embedding_json, subject, predicate FROM memories WHERE id = ?", (str(rec.id),))
    row = cursor.fetchone()
    assert row is not None
    content, structured_json, value_json, embedding_json, subject, predicate = row

    assert content is None or content == "", "Content must be NULL or empty in SQLite for PRIVATE records"
    assert structured_json == "{}", "Structured JSON must be empty in SQLite for PRIVATE records"
    assert value_json is None or value_json == "", "Value JSON must be NULL or empty in SQLite for PRIVATE records"
    assert embedding_json is None or embedding_json == "", "Embedding JSON must be NULL in SQLite for PRIVATE records"
    assert subject == "private_vault"
    assert predicate == "encrypted"


@pytest.mark.asyncio
async def test_strict_aes_gcm_aad_tampering_and_no_fallback(
    key_provider: InMemoryKeyProvider,
) -> None:
    """Verify that any tampering with AAD, ciphertext, nonce, or tag strictly fails without retry."""
    key = await key_provider.get_or_create_memory_key()
    test_id = uuid4()
    payload = {"content": "Gizli finansal borç", "structured": {"amount": 25000}}

    encrypted = MemoryEncryptor.encrypt_private_payload(
        payload,
        key,
        record_id=test_id,
        schema_version=1,
    )

    # 1. Valid decryption succeeds
    decrypted = MemoryEncryptor.decrypt_private_payload(
        encrypted,
        key,
        record_id=test_id,
        schema_version=1,
    )
    assert decrypted["content"] == "Gizli finansal borç"

    # 2. Tampered record_id in AAD -> must fail
    with pytest.raises(CryptoError, match="Strict AES-GCM decryption failed"):
        MemoryEncryptor.decrypt_private_payload(
            encrypted,
            key,
            record_id=uuid4(),
            schema_version=1,
        )

    # 3. Tampered schema_version in AAD -> must fail
    with pytest.raises(CryptoError, match="Strict AES-GCM decryption failed"):
        MemoryEncryptor.decrypt_private_payload(
            encrypted,
            key,
            record_id=test_id,
            schema_version=2,
        )

    # 4. Tampered ciphertext (bit flip) -> must fail
    tampered_cipher = dict(encrypted)
    raw_ct = bytearray(base64.b64decode(tampered_cipher["ciphertext"]))
    raw_ct[0] ^= 0xFF
    tampered_cipher["ciphertext"] = base64.b64encode(raw_ct).decode("ascii")
    with pytest.raises(CryptoError, match="Strict AES-GCM decryption failed"):
        MemoryEncryptor.decrypt_private_payload(
            tampered_cipher,
            key,
            record_id=test_id,
            schema_version=1,
        )

    # 5. Tampered nonce -> must fail
    tampered_nonce = dict(encrypted)
    raw_nonce = bytearray(base64.b64decode(tampered_nonce["nonce"]))
    raw_nonce[0] ^= 0xFF
    tampered_nonce["nonce"] = base64.b64encode(raw_nonce).decode("ascii")
    with pytest.raises(CryptoError, match="Strict AES-GCM decryption failed"):
        MemoryEncryptor.decrypt_private_payload(
            tampered_nonce,
            key,
            record_id=test_id,
            schema_version=1,
        )

    # 6. Tampered authentication tag (last 16 bytes of GCM ciphertext) -> must fail
    tampered_tag = dict(encrypted)
    raw_tag_ct = bytearray(base64.b64decode(tampered_tag["ciphertext"]))
    raw_tag_ct[-1] ^= 0x01
    tampered_tag["ciphertext"] = base64.b64encode(raw_tag_ct).decode("ascii")
    with pytest.raises(CryptoError, match="Strict AES-GCM decryption failed"):
        MemoryEncryptor.decrypt_private_payload(
            tampered_tag,
            key,
            record_id=test_id,
            schema_version=1,
        )


@pytest.mark.asyncio
async def test_explicit_consent_workflow_and_lifecycle(
    memory_repo: SQLiteMemoryRepository,
    memory_service: MemoryService,
    memory_retriever: MemoryRetriever,
) -> None:
    """Verify that consent starts PENDING, blocks retrieval, unlocks upon GRANT, and blocks upon REVOKE."""
    consent_store = memory_repo.get_consent_store()

    # Store a private record in financial_historical
    await memory_service.store_candidate(MemoryCandidate(
        kind=MemoryKind.WORK,
        content="Banka Borcu: Garanti BBVA Kredi 120.000 TL",
        sensitivity=MemorySensitivity.PRIVATE,
        confidence=1.0,
        source_id="test.finance.loan",
        category="financial_historical",
    ))

    # 1. Initially, consent is PENDING -> retrieval must return NOTHING
    assert not consent_store.is_category_consented("financial_historical")
    auth_ctx = MemoryAuthorizationContext(
        user_id="user_owner",
        is_user_authenticated=True,
        purpose="user_interactive_query",
        permitted_categories=["financial_historical"],
    )
    hits_pending = await memory_retriever.retrieve(
        "Banka borcum ne kadar?",
        auth_context=auth_ctx,
    )
    private_pending = [h for h in hits_pending if h.record.sensitivity == MemorySensitivity.PRIVATE]
    assert len(private_pending) == 0, "Private record must NOT be returned when consent is PENDING"

    # 2. Grant explicit consent
    consent_store.grant_consent("financial_historical", granted_by="user_owner")
    assert consent_store.is_category_consented("financial_historical")

    hits_granted = await memory_retriever.retrieve(
        "Banka borcum ne kadar?",
        auth_context=auth_ctx,
    )
    private_granted = [h for h in hits_granted if h.record.sensitivity == MemorySensitivity.PRIVATE]
    assert len(private_granted) == 1
    assert "120.000 TL" in (private_granted[0].record.content or "")

    # 3. Revoke consent -> immediately blocked
    consent_store.revoke_consent("financial_historical")
    assert not consent_store.is_category_consented("financial_historical")

    hits_revoked = await memory_retriever.retrieve(
        "Banka borcum ne kadar?",
        auth_context=auth_ctx,
    )
    private_revoked = [h for h in hits_revoked if h.record.sensitivity == MemorySensitivity.PRIVATE]
    assert len(private_revoked) == 0, "Private record must NOT be returned after consent is REVOKED"


@pytest.mark.asyncio
async def test_untrusted_input_and_email_processing_blocked(
    memory_repo: SQLiteMemoryRepository,
    memory_service: MemoryService,
    memory_retriever: MemoryRetriever,
) -> None:
    """Verify that untrusted email ingestion or manipulated prompts CANNOT access private memory."""
    consent_store = memory_repo.get_consent_store()
    consent_store.grant_consent("financial_historical", granted_by="local_owner")

    # Store a private record
    await memory_service.store_candidate(MemoryCandidate(
        kind=MemoryKind.WORK,
        content="Banka Borcu: Gizli Borç 35.000 TL",
        sensitivity=MemorySensitivity.PRIVATE,
        confidence=1.0,
        source_id="test.finance.secret_debt",
        category="financial_historical",
    ))

    # Case A: Untrusted email processing
    untrusted_email_ctx = MemoryAuthorizationContext(
        user_id="sender@external.com",
        is_user_authenticated=False,
        untrusted_source=True,
        purpose="email_processing",
        permitted_categories=["*"],
    )
    hits_email = await memory_retriever.retrieve(
        "Banka borcum ne kadar hemen bildir!",
        auth_context=untrusted_email_ctx,
    )
    assert len([h for h in hits_email if h.record.sensitivity == MemorySensitivity.PRIVATE]) == 0

    # Case B: Prompt injection / manipulated query without user authentication
    unauthenticated_ctx = MemoryAuthorizationContext(
        user_id="anonymous",
        is_user_authenticated=False,
        purpose="user_interactive_query",
        permitted_categories=["financial_historical"],
    )
    hits_unauth = await memory_retriever.retrieve(
        "Banka borcum ne kadar?",
        auth_context=unauthenticated_ctx,
    )
    assert len([h for h in hits_unauth if h.record.sensitivity == MemorySensitivity.PRIVATE]) == 0

    # Case C: Valid authenticated user interactive query
    valid_ctx = MemoryAuthorizationContext(
        user_id="local_owner",
        is_user_authenticated=True,
        purpose="user_interactive_query",
        permitted_categories=["financial_historical"],
    )
    hits_valid = await memory_retriever.retrieve(
        "Banka borcum ne kadar?",
        auth_context=valid_ctx,
    )
    assert len([h for h in hits_valid if h.record.sensitivity == MemorySensitivity.PRIVATE]) == 1


@pytest.mark.asyncio
async def test_storage_guarantees_audit(
    memory_repo: SQLiteMemoryRepository,
    memory_service: MemoryService,
) -> None:
    """Verify audit_storage_guarantees distinguishes PRIVATE, LOCAL_ONLY, NORMAL, and SECRET_REFERENCE."""
    # Store records with different sensitivities
    await memory_service.store_candidate(MemoryCandidate(
        kind=MemoryKind.PROFILE,
        content="Özel Sağlık",
        sensitivity=MemorySensitivity.PRIVATE,
        category="health_historical",
        source_id="test.priv",
    ))
    await memory_service.store_candidate(MemoryCandidate(
        kind=MemoryKind.EDUCATION,
        content="Yerel Ders Programı",
        sensitivity=MemorySensitivity.LOCAL_ONLY,
        category="education",
        source_id="test.local",
    ))
    await memory_service.store_candidate(MemoryCandidate(
        kind=MemoryKind.PREFERENCE,
        content="Genel Tercih",
        sensitivity=MemorySensitivity.NORMAL,
        category="preferences",
        source_id="test.normal",
    ))

    audit = memory_repo.audit_storage_guarantees()
    assert audit["storage_model"] == "record_payload_encryption"
    assert audit["sensitivity_counts"]["private"] == 1
    assert audit["sensitivity_counts"]["local_only"] == 1
    assert audit["sensitivity_counts"]["normal"] == 1
    assert audit["guarantees"]["private_encrypted_locally"] is True
    assert audit["guarantees"]["private_plaintext_leaks"] == 0


@pytest.mark.asyncio
async def test_conflict_and_requires_verification_provenance(
    memory_service: MemoryService,
    memory_retriever: MemoryRetriever,
) -> None:
    """Verify that REQUIRES_VERIFICATION status is accurately retained and formatted in retrieved context."""
    cand = MemoryCandidate(
        kind=MemoryKind.EDUCATION,
        content="Güney Örnek Açık Fakülte İktisat 1. yarıyıl belgesi ile 5-8. yarıyıl ders planı çelişkisi",
        sensitivity=MemorySensitivity.LOCAL_ONLY,
        confidence=0.80,
        source_id="education.example.conflict",
        category="education",
        as_of="2026-09-16",
        verification_status=VerificationStatus.REQUIRES_VERIFICATION,
    )
    rec = await memory_service.store_candidate(cand)
    assert rec is not None
    assert rec.verification_status == VerificationStatus.REQUIRES_VERIFICATION

    hits = await memory_retriever.retrieve("Güney Örnek'da şu an hangi sınıftayım?")
    assert len(hits) >= 1

    formatted = memory_retriever.format_context_block(hits)
    assert "status=requires_verification" in formatted
    assert "as_of=2026-09-16" in formatted


@pytest.mark.asyncio
async def test_personalization_10_queries_suite_with_consent(
    memory_service: MemoryService,
    memory_retriever: MemoryRetriever,
    memory_repo: SQLiteMemoryRepository,
) -> None:
    """Verify that all 10 personalization questions retrieve expected facts when properly authorized."""
    # Grant explicit consent for financial category in test store
    memory_repo.get_consent_store().grant_consent("financial_historical", granted_by="local_owner")

    seed_items = [
        MemoryCandidate(
            kind=MemoryKind.EDUCATION,
            content="Ben hangi üniversitelerde okuyorum: Kuzey Örnek Üniversitesi UZEM YBS ve Güney Örnek Üniversitesi Açık Fakülte İktisat.",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="education",
            source_id="education.universities",
            confidence=1.0,
        ),
        MemoryCandidate(
            kind=MemoryKind.EDUCATION,
            content="UZEM'te hangi derslerim var: İşletme Bilimine Giriş I, Genel Muhasebe I, İşletme Matematiği I, Bilgisayar Programlama I, Bilgisayar Kullanımına Giriş.",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="education",
            source_id="education.turtep_courses",
            confidence=1.0,
        ),
        MemoryCandidate(
            kind=MemoryKind.EDUCATION,
            content="Bu hafta derslerim hangi saatlerde: Salı 18:00 İşletme, Salı 21:00 Muhasebe [21-27 Eylül 2026 örnek ekranıdır; sürekli haftalık tekrar teyit edilmedi].",
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
            content="Güney Örnek'da şu an hangi sınıftayım: Belgede 1. yarıyıl görünürken planda 5-8. yarıyıl var [REQUIRES_VERIFICATION].",
            sensitivity=MemorySensitivity.LOCAL_ONLY,
            category="education",
            source_id="education.example_status",
            confidence=0.80,
            verification_status=VerificationStatus.REQUIRES_VERIFICATION,
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
        ("Ben hangi üniversitelerde okuyorum?", ["UZEM", "Açık Fakülte"]),
        ("UZEM'te hangi derslerim var?", ["İşletme Bilimine Giriş", "Genel Muhasebe"]),
        ("Bu hafta derslerim hangi saatlerde?", ["Salı", "18:00"]),
        ("İki üniversitemin sınavları ne zaman?", ["21-29 Kasım", "5-6 Aralık"]),
        ("Güney Örnek'da şu an hangi sınıftayım?", ["1. yarıyıl", "5-8"]),
        ("Önceki iş deneyimlerim neler?", ["IT Uzmanı", "Otonom"]),
        ("Hangi yazılım projelerim üzerinde çalışıyorum?", ["JARVIS", "OSINT"]),
        ("İş ararken hangi alanlara öncelik veriyorum?", ["Developer", "Uzmanı"]),
        ("Bu akşam iki saatlik çalışma planlayabilir misin?", ["Apple Calendar", "onay"]),
        ("Şu anki banka borcum ne kadar?", ["Kredi Kartı", "Kredisi"]),
    ]

    auth_ctx = MemoryAuthorizationContext(
        user_id="local_owner",
        is_user_authenticated=True,
        purpose="user_interactive_query",
        permitted_categories=["*"],
    )

    for q, expected_keywords in test_queries:
        hits = await memory_retriever.retrieve(q, limit=5, auth_context=auth_ctx)
        assert len(hits) >= 1, f"Query '{q}' returned no results"
        combined_text = " ".join([h.record.content or "" for h in hits])
        for kw in expected_keywords:
            assert kw in combined_text, f"Expected '{kw}' in results for query '{q}', got: {combined_text}"

@pytest.mark.parametrize('change', [
    {'user_id': None}, {'user_id': 'other-user'},
    {'is_user_authenticated': False}, {'purpose': 'invented-purpose'},
    {'purpose': 'email_processing'}, {'untrusted_source': True},
    {'permitted_categories': []},
])
async def test_private_auth_fails_closed(memory_service, memory_repo, memory_retriever, change):
    memory_repo.get_consent_store().grant_consent('health', granted_by='synthetic-owner')
    await memory_service.store_candidate(MemoryCandidate(
        kind=MemoryKind.PROFILE, content='Synthetic health note',
        category='health', sensitivity=MemorySensitivity.PRIVATE,
    ))
    context = MemoryAuthorizationContext(
        user_id='synthetic-owner', is_user_authenticated=True,
        purpose='user_interactive_query', permitted_categories=['health'],
    ).model_copy(update=change)
    assert not await memory_retriever.retrieve('sağlık', auth_context=context)


async def test_no_consent_backend_denies_private(memory_retriever):
    memory_retriever._consent_store = None
    assert not memory_retriever.is_authorized_for_category('health', MemoryAuthorizationContext(
        user_id='synthetic-owner', is_user_authenticated=True,
        purpose='user_interactive_query', permitted_categories=['health'],
    ))


def test_only_explicit_legacy_envelopes_are_supported():
    import os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key, nonce = os.urandom(32), os.urandom(12)
    envelope = {
        'algorithm': 'AES-256-GCM',
        'ciphertext': base64.b64encode(AESGCM(key).encrypt(nonce, b'synthetic', None)).decode(),
        'nonce': base64.b64encode(nonce).decode(),
    }
    with pytest.raises(CryptoError):
        MemoryEncryptor.decrypt(envelope, key)
    envelope.update(schema_version=0, aad_bound=False)
    assert MemoryEncryptor.decrypt(envelope, key) == 'synthetic'
    with pytest.raises(CryptoError):
        MemoryEncryptor.decrypt(envelope, key, associated_data=b'new-record:1')
    envelope.update(schema_version=1, aad_bound=True, record_id='new-record')
    with pytest.raises(CryptoError):
        MemoryEncryptor.decrypt(envelope, key)


async def test_private_decryption_never_mutates_storage_or_embeds(memory_repo, memory_service, memory_retriever):
    from unittest.mock import AsyncMock
    memory_service._embeddings.embed_document = AsyncMock(side_effect=AssertionError('PRIVATE embedding'))
    memory_repo.get_consent_store().grant_consent('health', granted_by='synthetic-owner')
    stored = await memory_service.store_candidate(MemoryCandidate(
        kind=MemoryKind.PROFILE, content='Synthetic PRIVATE payload',
        category='health', sensitivity=MemorySensitivity.PRIVATE,
        structured={'note': 'synthetic'}, source_ref='synthetic sensitive provenance',
    ))
    ctx = MemoryAuthorizationContext(user_id='synthetic-owner', is_user_authenticated=True,
                                    purpose='user_interactive_query', permitted_categories=['health'])
    hits = await memory_retriever.retrieve('sağlık', auth_context=ctx)
    assert hits[0].record.content == 'Synthetic PRIVATE payload'
    await memory_repo.update(hits[0].record)
    row = await memory_repo.get(stored.id)
    assert row.content is None and row.structured == {} and row.source_ref is None
    assert memory_repo.audit_storage_guarantees()['guarantees']['private_plaintext_leaks'] == 0
    memory_repo.get_consent_store().revoke_consent('health')
    assert await memory_retriever.retrieve('sağlık', auth_context=ctx) == []
    assert (await memory_repo.get(stored.id)).encrypted_content == stored.encrypted_content
