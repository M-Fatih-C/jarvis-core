"""Extraction pipeline for discovering memory candidates from conversation turns."""

from __future__ import annotations

import json
import re
from typing import Any
from core.llm.base import LLMAdapter
from core.logging.setup import get_logger
from core.memory.models import (
    MemoryCandidate,
    MemoryKind,
    MemorySensitivity,
    MemorySourceType,
)
from core.models.messages import ChatMessage, MessageRole

logger = get_logger("jarvis.memory.extractor")

EXTRACTION_SYSTEM_PROMPT = """You are the Jarvis Memory Extractor. Analyze the user statement and assistant response to extract durable, persistent facts and preferences about the user.

Rules:
1. Extract ONLY facts or preferences that are long-term (e.g., routines, study/work domains, explicit preferences, profile details).
2. Do NOT extract transient intents, ephemeral questions, or temporary tool outputs.
3. Respond ONLY with a valid JSON array of objects. Do not include markdown codeblocks, thinking tokens, or explanatory text.
4. Each JSON object MUST have these fields:
   - "kind": one of ["profile", "preference", "person", "education", "work", "project", "routine", "decision"]
   - "content": concise normalized summary in English or Turkish
   - "subject": "user" or relevant entity
   - "predicate": normalized property name (e.g. "side_project_work_time", "field_of_study", "preferred_editor")
   - "value": string or structured value (e.g. "after_18_weekdays")
   - "structured": key-value dictionary with details
   - "sensitivity": "normal" or "private"
   - "confidence": float between 0.0 and 1.0
   - "importance": float between 0.0 and 1.0
   - "source_type": "explicit_user" or "inferred"
   - "durable": true or false

If nothing durable is to be learned, output an empty JSON array: []
"""


class MemoryExtractor:
    """Extracts MemoryCandidate proposals from conversation turns using local Qwen or rule-based heuristics."""

    def __init__(self, llm: LLMAdapter | None = None) -> None:
        self._llm = llm

    def _rule_based_extract(self, user_text: str) -> list[MemoryCandidate]:
        """Fast, deterministic fallback heuristics for explicit user preferences."""
        candidates: list[MemoryCandidate] = []
        u_lower = user_text.lower()

        # Preference: Side projects / Yan projeler after a certain time
        m_side = re.search(r"(?:yan\s+proje(?:ler)?|side\s+project(?:s)?).*?(\d{1,2}[:.]\d{2})['\s]*(?:den|dan|sonra|after)", u_lower)
        if m_side:
            time_val = m_side.group(1).replace(".", ":")
            candidates.append(MemoryCandidate(
                kind=MemoryKind.PREFERENCE,
                content=f"User prefers working on side projects after {time_val} on weekdays.",
                subject="user",
                predicate="side_project_work_time",
                value=f"after_{time_val.replace(':', '_')}_weekdays",
                structured={"domain": "schedule", "days": "weekdays", "after": time_val},
                sensitivity=MemorySensitivity.NORMAL,
                confidence=0.98,
                importance=0.85,
                source_type=MemorySourceType.EXPLICIT_USER,
                durable=True,
                training_eligible=True,
            ))

        # Education: YBS / Management Information Systems
        if "ybs" in u_lower or "yönetim bilişim" in u_lower or "management information systems" in u_lower:
            candidates.append(MemoryCandidate(
                kind=MemoryKind.EDUCATION,
                content="User is studying or involved in Management Information Systems (YBS).",
                subject="user",
                predicate="field_of_study",
                value="management_information_systems",
                structured={"field": "YBS", "degree": "Management Information Systems"},
                sensitivity=MemorySensitivity.NORMAL,
                confidence=0.95,
                importance=0.80,
                source_type=MemorySourceType.EXPLICIT_USER,
                durable=True,
                training_eligible=True,
            ))

        return candidates

    async def extract_candidates(
        self,
        user_message: str,
        assistant_response: str = "",
        existing_memories_summary: str = "",
    ) -> list[MemoryCandidate]:
        """Extract memory proposals from conversation turn."""
        candidates: list[MemoryCandidate] = []

        # 1. Rule-based extraction (instant & deterministic)
        rule_candidates = self._rule_based_extract(user_message)
        candidates.extend(rule_candidates)

        # 2. LLM-based extraction if adapter is present
        if self._llm is not None:
            try:
                extraction_prompt = (
                    f"User Statement: {user_message}\n"
                    f"Assistant Response: {assistant_response}\n"
                    f"Existing Known Memories: {existing_memories_summary}\n\n"
                    "Extract new durable memory candidates as JSON array:"
                )
                messages = [
                    ChatMessage(role=MessageRole.SYSTEM, content=EXTRACTION_SYSTEM_PROMPT),
                    ChatMessage(role=MessageRole.USER, content=extraction_prompt),
                ]
                resp = await self._llm.generate(messages)
                raw_text = resp.content or ""

                # Strip possible markdown codeblocks
                raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.MULTILINE)
                raw_text = re.sub(r"```$", "", raw_text.strip(), flags=re.MULTILINE)

                # Find outermost JSON array
                start = raw_text.find("[")
                end = raw_text.rfind("]")
                if start != -1 and end != -1:
                    json_str = raw_text[start : end + 1]
                    parsed = json.loads(json_str)
                    if isinstance(parsed, list):
                        for item in parsed:
                            try:
                                cand = MemoryCandidate(**item)
                                # Avoid duplicating if rule-based already caught it
                                if not any(c.predicate == cand.predicate and c.subject == cand.subject for c in candidates):
                                    candidates.append(cand)
                            except Exception as item_err:
                                logger.debug("skipped_invalid_candidate_item", error=str(item_err))
            except Exception as exc:
                logger.warning("llm_memory_extraction_failed", error=str(exc))

        return candidates
