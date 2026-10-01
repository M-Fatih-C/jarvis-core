"""Extracts structured, unapproved task proposals from analyzed emails without automatic execution."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from core.email_analysis.schemas import (
    DeadlineConfidence,
    EmailAnalysisResult,
    EmailCategory,
    ImportanceLevel,
    TaskPriority,
    TaskProposal,
)
from core.logging.setup import get_logger

if TYPE_CHECKING:
    from integrations.gmail.models import NormalizedEmail

logger = get_logger("jarvis.email_analysis.task_extractor")


class TaskExtractor:
    """Extracts structured TaskProposal instances from emails and analysis results."""

    @staticmethod
    def extract_task_proposal(
        email: NormalizedEmail,
        analysis: EmailAnalysisResult,
    ) -> TaskProposal | None:
        """Generate a TaskProposal if the email contains an actionable task or urgent response.
        
        Task proposals are stored with status='proposed'.
        They are NEVER executed automatically in Milestone 4.1.
        """
        # Determine if email is actionable
        is_actionable = (
            analysis.contains_task
            or analysis.has_deadline
            or (analysis.requires_response and analysis.importance in (ImportanceLevel.HIGH, ImportanceLevel.MEDIUM))
            or (analysis.security_or_payment and analysis.importance == ImportanceLevel.HIGH)
        )

        if not is_actionable:
            return None

        # Map importance to task priority
        if analysis.importance == ImportanceLevel.HIGH:
            priority = TaskPriority.HIGH
        elif analysis.importance == ImportanceLevel.LOW:
            priority = TaskPriority.LOW
        else:
            priority = TaskPriority.MEDIUM

        # Determine task title
        if analysis.extracted_task_title and analysis.extracted_task_title.strip():
            title = analysis.extracted_task_title.strip()
        elif analysis.requires_response:
            title = f"Yanıtla: {email.subject}"
        elif analysis.has_deadline:
            title = f"Son Tarih: {email.subject}"
        else:
            title = email.subject or "E-posta Görevi"

        # Determine task description
        description = (
            analysis.extracted_task_description
            or analysis.summary
            or f"Gönderen: {email.sender}\nKonu: {email.subject}"
        )

        # Propose appropriate action (e.g. reminder, calendar review, email review)
        if analysis.proposed_action and analysis.proposed_action.strip():
            proposed_action = analysis.proposed_action.strip()
        elif analysis.has_deadline:
            proposed_action = "Hatırlatıcı oluştur"
        elif analysis.requires_response:
            proposed_action = "E-postayı yanıtla"
        else:
            proposed_action = "E-postayı incele"

        proposal = TaskProposal(
            task_id=uuid4(),
            source_message_id=email.message_id,
            title=title,
            description=description,
            category=analysis.category,
            priority=priority,
            deadline=analysis.parsed_deadline,
            deadline_confidence=analysis.deadline_confidence,
            raw_deadline_text=analysis.raw_deadline_text,
            proposed_action=proposed_action,
            status="proposed",
            created_at=datetime.now(timezone.utc),
        )

        logger.info(
            "task_proposal_extracted",
            task_id=str(proposal.task_id),
            msg_id=email.message_id,
            priority=proposal.priority.value,
            has_deadline=proposal.deadline is not None,
        )
        return proposal
