"""Deduplication and contradiction detection for memory records."""

import hashlib
import json
import re
from typing import Any
from uuid import UUID
from core.memory.models import (
    MemoryCandidate,
    MemoryRecord,
    MemorySourceType,
    MemoryStatus,
)


class DeduplicationEngine:
    """Computes deterministic fingerprints and resolves memory contradictions."""

    @staticmethod
    def normalize_text(text: str) -> str:
        """Lowercase and collapse whitespace."""
        return re.sub(r"\s+", " ", text.strip().lower())

    @classmethod
    def generate_fingerprint(
        cls,
        kind: str,
        content: str | None,
        subject: str | None = None,
        predicate: str | None = None,
        structured: dict[str, Any] | None = None,
    ) -> str:
        """Generate a deterministic SHA-256 fingerprint for a memory item.
        
        Args:
            kind: MemoryKind value string.
            content: Raw or decrypted text content.
            subject: Optional subject entity.
            predicate: Optional predicate / property.
            structured: Optional structured key-value payload.
            
        Returns:
            64-character hexadecimal SHA-256 string.
        """
        norm_kind = kind.lower().strip()
        norm_subj = cls.normalize_text(subject or "")
        norm_pred = cls.normalize_text(predicate or "")
        norm_content = cls.normalize_text(content or "")

        # Canonicalize structured dict
        if structured:
            canonical_struct = json.dumps(structured, sort_keys=True, ensure_ascii=False)
        else:
            canonical_struct = ""

        raw_key = f"{norm_kind}|{norm_subj}|{norm_pred}|{norm_content}|{canonical_struct}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @classmethod
    def find_contradictions(
        cls,
        candidate: MemoryCandidate,
        existing_active_records: list[MemoryRecord],
    ) -> list[MemoryRecord]:
        """Find active existing records that conflict with the incoming candidate.
        
        A contradiction occurs when an incoming EXPLICIT_USER candidate has the same
        non-empty subject and predicate as an active existing memory, but a different value/content.
        
        Args:
            candidate: Candidate to check.
            existing_active_records: Currently active records in the store.
            
        Returns:
            List of existing records that should be superseded.
        """
        # Only explicit user inputs automatically supersede old active memories
        if candidate.source_type != MemorySourceType.EXPLICIT_USER:
            return []

        if not candidate.subject or not candidate.predicate:
            return []

        cand_subj = cls.normalize_text(candidate.subject)
        cand_pred = cls.normalize_text(candidate.predicate)

        contradictions: list[MemoryRecord] = []
        for record in existing_active_records:
            if record.status != MemoryStatus.ACTIVE:
                continue
            if not record.subject or not record.predicate:
                continue

            rec_subj = cls.normalize_text(record.subject)
            rec_pred = cls.normalize_text(record.predicate)

            if cand_subj == rec_subj and cand_pred == rec_pred:
                # Same subject & predicate: check if value or content changed
                val_diff = str(record.value) != str(candidate.value) if candidate.value is not None else False
                content_diff = cls.normalize_text(record.content or "") != cls.normalize_text(candidate.content)

                if val_diff or content_diff:
                    contradictions.append(record)

        return contradictions
