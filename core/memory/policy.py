"""Deterministic secret filtering and memory persistence policies."""

import re
from typing import Any
from pydantic import BaseModel, Field
from core.logging.setup import get_logger
from core.memory.models import (
    MemoryCandidate,
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
)

logger = get_logger("jarvis.memory.policy")

# Regex patterns for deterministic secret detection
SECRET_PATTERNS = [
    # API Keys & Tokens
    (re.compile(r"\b(sk-[a-zA-Z0-9]{20,})\b"), "openai_api_key"),
    (re.compile(r"\b(ghp_[a-zA-Z0-9]{36,})\b"), "github_pat"),
    (re.compile(r"\b(gho_[a-zA-Z0-9]{36,})\b"), "github_oauth"),
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), "aws_access_key"),
    (re.compile(r"\b(AIza[0-9A-Za-z\-_]{35})\b"), "google_api_key"),
    (re.compile(r"\b(bearer\s+[a-zA-Z0-9_\-\.]{20,})\b", re.IGNORECASE), "bearer_token"),
    # Private Keys
    (re.compile(r"-----BEGIN (?:[A-Z ]+)?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z ]+)?PRIVATE KEY-----"), "private_key"),
    # Credit Card Numbers (13 to 19 digits)
    (re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"), "credit_card_number"),
    # Generic password assignments in text
    (re.compile(r"(?:password|passwd|parola|şifre)\w*\s*[:=]\s*(\S+)", re.IGNORECASE), "password_assignment"),
]

SENSITIVE_FIELD_NAMES = {
    "password", "passwd", "secret", "api_key", "token", "access_token",
    "refresh_token", "private_key", "client_secret", "auth", "credential",
    "parola", "sifre",
}


class SecretFilterResult(BaseModel):
    """Result of deterministic secret analysis."""
    has_secret: bool
    detected_types: list[str] = Field(default_factory=list)
    redacted_content: str
    redacted_structured: dict[str, Any] = Field(default_factory=dict)
    suggested_secret_ref: str | None = None


class MemoryPolicyDecision(BaseModel):
    """Decision produced by MemoryPolicy for candidate persistence."""
    accepted: bool
    initial_status: MemoryStatus
    final_sensitivity: MemorySensitivity
    cleaned_content: str
    cleaned_structured: dict[str, Any] = Field(default_factory=dict)
    training_eligible: bool
    reason: str


class SecretFilter:
    """Deterministic filter for scanning and redacting secrets from memory inputs."""

    @classmethod
    def scan_and_redact_text(cls, text: str) -> tuple[bool, list[str], str, str | None]:
        """Scan text with deterministic regex patterns, redacting any detected secret.
        
        Returns:
            (has_secret, detected_types, redacted_text, suggested_secret_ref)
        """
        has_secret = False
        detected_types: list[str] = []
        redacted = text
        suggested_ref: str | None = None

        for pattern, sec_type in SECRET_PATTERNS:
            matches = list(pattern.finditer(redacted))
            if matches:
                has_secret = True
                if sec_type not in detected_types:
                    detected_types.append(sec_type)
                if suggested_ref is None:
                    suggested_ref = f"secret_ref_{sec_type}"
                # Redact match
                redacted = pattern.sub(f"[REDACTED_SECRET:{sec_type.upper()}]", redacted)

        return has_secret, detected_types, redacted, suggested_ref

    @classmethod
    def scan_and_redact_structured(cls, data: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
        """Recursively scan dictionary keys and values for sensitive fields."""
        has_secret = False
        detected_types: list[str] = []
        clean_dict: dict[str, Any] = {}

        for k, v in data.items():
            k_lower = str(k).lower()
            if any(sens in k_lower for sens in SENSITIVE_FIELD_NAMES):
                has_secret = True
                detected_types.append(f"field:{k}")
                clean_dict[k] = "[REDACTED_SENSITIVE_FIELD]"
            elif isinstance(v, str):
                s_has, s_types, s_red, _ = cls.scan_and_redact_text(v)
                if s_has:
                    has_secret = True
                    detected_types.extend(s_types)
                    clean_dict[k] = s_red
                else:
                    clean_dict[k] = v
            elif isinstance(v, dict):
                sub_has, sub_types, sub_dict = cls.scan_and_redact_structured(v)
                if sub_has:
                    has_secret = True
                    detected_types.extend(sub_types)
                clean_dict[k] = sub_dict
            else:
                clean_dict[k] = v

        return has_secret, list(set(detected_types)), clean_dict

    @classmethod
    def filter(cls, content: str, structured: dict[str, Any]) -> SecretFilterResult:
        """Run complete deterministic text and structured filter."""
        text_has, text_types, text_red, text_ref = cls.scan_and_redact_text(content)
        struct_has, struct_types, struct_red = cls.scan_and_redact_structured(structured)

        all_has = text_has or struct_has
        all_types = list(set(text_types + struct_types))
        suggested_ref = text_ref or (f"secret_ref_{all_types[0]}" if all_types else None)

        return SecretFilterResult(
            has_secret=all_has,
            detected_types=all_types,
            redacted_content=text_red,
            redacted_structured=struct_red,
            suggested_secret_ref=suggested_ref,
        )


class MemoryPolicy:
    """Automated gatekeeper applying rules for memory retention, security, and classification."""

    def __init__(self, secret_filter: type[SecretFilter] = SecretFilter) -> None:
        self.secret_filter = secret_filter

    def evaluate_candidate(self, candidate: MemoryCandidate) -> MemoryPolicyDecision:
        """Evaluate a MemoryCandidate against security and lifecycle retention rules.
        
        Args:
            candidate: Candidate extracted from conversation.
            
        Returns:
            MemoryPolicyDecision with sanitized contents and initial state.
        """
        # 1. Deterministic Secret Filtering
        filter_res = self.secret_filter.filter(candidate.content, candidate.structured)

        final_sensitivity = candidate.sensitivity
        cleaned_content = filter_res.redacted_content
        cleaned_structured = filter_res.redacted_structured

        if filter_res.has_secret:
            logger.warning(
                "secrets_detected_in_memory_candidate",
                detected_types=filter_res.detected_types,
            )
            # Downgrade to SECRET_REFERENCE to ensure no raw secrets or embeddings
            final_sensitivity = MemorySensitivity.SECRET_REFERENCE
            if candidate.subject:
                cleaned_structured["secret_ref"] = filter_res.suggested_secret_ref

        # 2. Lifecycle Evaluation by Source and Confidence
        if final_sensitivity == MemorySensitivity.SECRET_REFERENCE:
            # Secret references: accept only metadata, never training eligible, local-focused
            return MemoryPolicyDecision(
                accepted=True,
                initial_status=MemoryStatus.ACTIVE if candidate.source_type == MemorySourceType.EXPLICIT_USER else MemoryStatus.CANDIDATE,
                final_sensitivity=MemorySensitivity.SECRET_REFERENCE,
                cleaned_content=cleaned_content,
                cleaned_structured=cleaned_structured,
                training_eligible=False,
                reason="Raw secret detected and redacted into secret_reference",
            )

        # Inferred memories: Default to CANDIDATE to prevent single-shot hallucinated memories
        if candidate.source_type == MemorySourceType.INFERRED:
            return MemoryPolicyDecision(
                accepted=True,
                initial_status=MemoryStatus.CANDIDATE,
                final_sensitivity=final_sensitivity,
                cleaned_content=cleaned_content,
                cleaned_structured=cleaned_structured,
                training_eligible=False,
                reason="Inferred memory held as candidate pending reinforcement or confirmation",
            )

        # Explicit user memories with high confidence:
        if candidate.confidence >= 0.8 and candidate.durable:
            training_ok = (final_sensitivity == MemorySensitivity.NORMAL and candidate.training_eligible)
            return MemoryPolicyDecision(
                accepted=True,
                initial_status=MemoryStatus.ACTIVE,
                final_sensitivity=final_sensitivity,
                cleaned_content=cleaned_content,
                cleaned_structured=cleaned_structured,
                training_eligible=training_ok,
                reason=f"High-confidence explicit {final_sensitivity.value} memory accepted as active",
            )

        # Low confidence explicit memory:
        return MemoryPolicyDecision(
            accepted=True,
            initial_status=MemoryStatus.CANDIDATE,
            final_sensitivity=final_sensitivity,
            cleaned_content=cleaned_content,
            cleaned_structured=cleaned_structured,
            training_eligible=False,
            reason=f"Low confidence ({candidate.confidence}) memory held as candidate",
        )
