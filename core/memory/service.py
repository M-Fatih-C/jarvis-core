"""High-level orchestration service for Jarvis Memory & Personalization."""

from __future__ import annotations

import json
from uuid import UUID
from core.config.settings import Settings, get_settings
from core.llm.base import LLMAdapter
from core.logging.setup import get_logger
from core.memory.crypto import InMemoryKeyProvider, KeyProvider, MemoryEncryptor
from core.memory.deduplication import DeduplicationEngine
from core.memory.embeddings.base import EmbeddingProvider
from core.memory.embeddings import get_embedding_provider
from core.memory.extractor import MemoryExtractor
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.models import (
    MemoryCandidate,
    MemoryFilters,
    MemoryKind,
    MemoryRecord,
    MemorySearchResult,
    MemorySensitivity,
    MemoryStatus,
)
from core.memory.policy import MemoryPolicy
from core.memory.ranking import MemoryRanker
from core.memory.repository import MemoryRepository
from core.memory.retrieval import MemoryRetriever

logger = get_logger("jarvis.memory.service")


class MemoryService:
    """Coordinates memory extraction, policy enforcement, encryption, local/cloud storage, and retrieval."""

    def __init__(
        self,
        repository: MemoryRepository | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        key_provider: KeyProvider | None = None,
        policy: MemoryPolicy | None = None,
        extractor: MemoryExtractor | None = None,
        retriever: MemoryRetriever | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repo = repository or SQLiteMemoryRepository(self._settings.memory_db_path)
        self._embeddings = embedding_provider or get_embedding_provider(
            provider_type=self._settings.embedding_provider,
            model_name=self._settings.embedding_model,
        )
        self._key_provider = key_provider or InMemoryKeyProvider()
        self._policy = policy or MemoryPolicy()
        self._extractor = extractor or MemoryExtractor()
        self._ranker = MemoryRanker(settings=self._settings)
        self._retriever = retriever or MemoryRetriever(
            repository=self._repo,
            embedding_provider=self._embeddings,
            ranker=self._ranker,
            key_provider=self._key_provider,
            settings=self._settings,
        )

    @property
    def repository(self) -> MemoryRepository:
        return self._repo

    @property
    def retriever(self) -> MemoryRetriever:
        return self._retriever

    async def retrieve(self, query: str, limit: int | None = None) -> list[MemorySearchResult]:
        """Convenience method delegating retrieval to the underlying MemoryRetriever."""
        return await self._retriever.retrieve(query=query, limit=limit)

    async def store_candidate(self, candidate: MemoryCandidate) -> MemoryRecord | None:
        """Process, validate, encrypt, embed, and persist a memory candidate proposal."""
        # 1. Evaluate candidate with MemoryPolicy
        decision = self._policy.evaluate_candidate(candidate)
        if not decision.accepted:
            logger.info("memory_candidate_rejected_by_policy", reason=decision.reason)
            return None

        # 2. Fingerprint & Deduplication
        fingerprint = DeduplicationEngine.generate_fingerprint(
            kind=candidate.kind.value,
            content=decision.cleaned_content,
            subject=candidate.subject,
            predicate=candidate.predicate,
            structured=decision.cleaned_structured,
        )

        existing = await self._repo.find_by_fingerprint(fingerprint)
        if existing and existing.status == MemoryStatus.ACTIVE:
            logger.info("memory_duplicate_found", memory_id=str(existing.id))
            return existing

        # 3. Contradiction Resolution
        active_records = await self._repo.list(filters=MemoryFilters(status=MemoryStatus.ACTIVE))
        contradictions = DeduplicationEngine.find_contradictions(candidate, active_records)

        superseded_id: UUID | None = None
        for old_rec in contradictions:
            logger.info(
                "memory_contradiction_superseded",
                old_id=str(old_rec.id),
                subject=old_rec.subject,
                predicate=old_rec.predicate,
            )
            old_rec.status = MemoryStatus.SUPERSEDED
            await self._repo.update(old_rec)
            superseded_id = old_rec.id

        # 4. Encryption & Sensitivity Handling
        content_to_store: str | None = decision.cleaned_content
        encrypted_content: str | None = None
        embedding: list[float] | None = None
        embedding_model_name: str | None = None

        if decision.final_sensitivity == MemorySensitivity.PRIVATE:
            # Client-side AES-256-GCM encryption
            key = await self._key_provider.get_or_create_memory_key()
            enc_dict = MemoryEncryptor.encrypt(decision.cleaned_content, key)
            encrypted_content = json.dumps(enc_dict)
            # Generate local-only embedding for local semantic retrieval
            embedding = await self._embeddings.embed_document(decision.cleaned_content)
            embedding_model_name = self._settings.embedding_model

        elif decision.final_sensitivity == MemorySensitivity.SECRET_REFERENCE:
            # Never embed secrets or secret references
            embedding = None

        else:
            # NORMAL or LOCAL_ONLY
            embedding = await self._embeddings.embed_document(decision.cleaned_content)
            embedding_model_name = self._settings.embedding_model

        # 5. Build and save persistent record
        record = MemoryRecord(
            kind=candidate.kind,
            sensitivity=decision.final_sensitivity,
            content=content_to_store,
            encrypted_content=encrypted_content,
            structured=decision.cleaned_structured,
            source_type=candidate.source_type,
            confidence=candidate.confidence,
            importance=candidate.importance,
            subject=candidate.subject,
            predicate=candidate.predicate,
            value=candidate.value,
            fingerprint=fingerprint,
            status=decision.initial_status,
            embedding=embedding,
            embedding_model=embedding_model_name,
            supersedes=superseded_id,
            training_eligible=decision.training_eligible,
        )

        saved = await self._repo.save(record)
        logger.info(
            "memory_stored_successfully",
            memory_id=str(saved.id),
            kind=saved.kind.value,
            sensitivity=saved.sensitivity.value,
            status=saved.status.value,
        )
        return saved

    async def retrieve_context_for_query(self, query: str) -> str:
        """Fetch and format relevant memory context block for inclusion in LLM prompt."""
        try:
            results = await self._retriever.retrieve(query)
            return self._retriever.format_context_block(results)
        except Exception as exc:
            logger.warning("memory_context_retrieval_failed", error=str(exc))
            return ""

    async def process_conversation_turn(
        self,
        user_message: str,
        assistant_response: str,
    ) -> list[MemoryRecord]:
        """Discover and store new memory candidates following an agent interaction."""
        stored: list[MemoryRecord] = []
        try:
            candidates = await self._extractor.extract_candidates(user_message, assistant_response)
            for cand in candidates:
                rec = await self.store_candidate(cand)
                if rec:
                    stored.append(rec)
        except Exception as exc:
            # Memory extraction must never break primary user flow
            logger.warning("memory_processing_turn_failed", error=str(exc))
        return stored
