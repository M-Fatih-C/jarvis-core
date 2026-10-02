"""Smart notification dispatch for important emails using native macOS notifications with privacy protection."""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from core.email_analysis.schemas import (
    EmailAnalysisResult,
    EmailCategory,
    ImportanceLevel,
    TaskProposal,
)
from core.logging.setup import get_logger
from integrations.gmail.models import NormalizedEmail
from integrations.gmail.storage import EmailStorage
from integrations.macos.client import MacBridgeClient

logger = get_logger("jarvis.notifications.email")


def sanitize_notification_body(text: str) -> str:
    """Sanitize sensitive credentials, OTPs, or passwords before displaying in notification banner."""
    # Mask 6-digit OTP codes
    sanitized = re.sub(r"\b\d{6}\b", "[KOD]", text)
    # Mask password tokens (including inflected Turkish forms like Şifreniz)
    sanitized = re.sub(r"(?i)(şifre[a-zçğıöşü]*|parola[a-zçğıöşü]*|password[a-z]*|pin|secret)[:=\s]+[^\s]+", r"\1: [GİZLENDİ]", sanitized)
    # Remove long URLs
    sanitized = re.sub(r"https?://[^\s]{30,}", "[Bağlantı]", sanitized)
    return sanitized.strip()


class NotificationDispatchResult:
    """Detailed result of a notification evaluation and dispatch attempt."""

    def __init__(
        self,
        dispatched: bool,
        status: str,
        reason: str = "",
        notification_id: str | None = None,
    ) -> None:
        self.dispatched = dispatched
        self.status = status  # delivered, skipped_unimportant, skipped_duplicate, permission_unavailable, bridge_failed
        self.reason = reason
        self.notification_id = notification_id

    def __bool__(self) -> bool:
        return self.dispatched

    def __repr__(self) -> str:
        return f"<NotificationDispatchResult dispatched={self.dispatched} status={self.status} reason='{self.reason}'>"


class EmailNotificationService:
    """Filters, deduplicates, and dispatches native macOS notifications for important emails."""

    def __init__(
        self,
        storage: EmailStorage,
        bridge_client: MacBridgeClient | None = None,
    ) -> None:
        self.storage = storage
        self.bridge_client = bridge_client

    def should_notify(self, analysis: EmailAnalysisResult) -> bool:
        """Determine whether an analyzed email warrants an immediate notification banner."""
        # Never notify for promotional or low-priority emails
        if analysis.category == EmailCategory.PROMOTIONAL or analysis.importance == ImportanceLevel.LOW:
            return False

        # 1. High importance emails always notify
        if analysis.importance == ImportanceLevel.HIGH:
            return True

        # 2. Urgent security alerts or payment notices
        if analysis.security_or_payment:
            return True

        # 3. Approaching education or examination deadlines
        if analysis.category == EmailCategory.EDUCATION and (analysis.has_deadline or analysis.contains_task):
            return True

        # 4. Important job application / career updates
        if analysis.category == EmailCategory.WORK_CAREER and analysis.importance in (
            ImportanceLevel.HIGH,
            ImportanceLevel.MEDIUM,
        ):
            return True

        # 5. Email explicitly requires response
        if analysis.requires_response:
            return True

        # 6. Meeting or appointment requiring attention
        if analysis.has_meeting:
            return True

        return False

    async def notify_if_important(
        self,
        email: NormalizedEmail,
        analysis: EmailAnalysisResult,
        task: TaskProposal | None = None,
    ) -> NotificationDispatchResult:
        """Evaluate and send native macOS notification if the email is important and not a duplicate.
        
        Returns:
            NotificationDispatchResult indicating delivery or explicit degraded/skipped reason.
        """
        if not self.should_notify(analysis):
            logger.debug("email_notification_skipped_not_important", msg_id=email.message_id)
            return NotificationDispatchResult(
                dispatched=False,
                status="skipped_unimportant",
                reason="Email is promotional or low priority",
            )

        # Deduplication check
        if await self.storage.is_notification_sent(email.message_id):
            logger.info("email_notification_skipped_already_sent", msg_id=email.message_id)
            return NotificationDispatchResult(
                dispatched=False,
                status="skipped_duplicate",
                reason=f"Notification already recorded for message {email.message_id}",
            )

        # Format notification contents concisely
        clean_subject = (email.subject[:60] + "...") if len(email.subject) > 60 else email.subject
        if analysis.importance == ImportanceLevel.HIGH:
            title = f"🔴 Jarvis: {clean_subject}"
        elif analysis.has_deadline:
            title = f"⏰ Jarvis: {clean_subject}"
        else:
            title = f"📬 Jarvis: {clean_subject}"

        body_prefix = f"Gönderen: {email.sender_email or email.sender}\n"
        clean_summary = sanitize_notification_body(analysis.summary)
        body = f"{body_prefix}{clean_summary}"
        if len(body) > 300:
            body = body[:297] + "..."

        notification_id = f"gmail_{email.message_id}"
        task_id_str = str(task.task_id) if task else None

        if self.bridge_client:
            try:
                # Check status first if possible or attempt show
                res = await self.bridge_client.call(
                    "notifications.show",
                    {
                        "title": title,
                        "body": body,
                        "identifier": notification_id,
                    },
                )
                # Verify that delivery actually succeeded
                if isinstance(res, dict) and res.get("status") in ("denied", "not_authorized", "failed"):
                    logger.warning("native_notification_permission_unavailable", status=res.get("status"))
                    return NotificationDispatchResult(
                        dispatched=False,
                        status="permission_unavailable",
                        reason=f"macOS notification permission is {res.get('status')}",
                    )

                logger.info("email_notification_dispatched", msg_id=email.message_id, title=title)
            except Exception as exc:
                logger.warning("email_notification_bridge_failed", error=str(exc))
                return NotificationDispatchResult(
                    dispatched=False,
                    status="bridge_failed",
                    reason=str(exc),
                )
        else:
            # Mock / headless environment without active bridge connection
            logger.info("email_notification_recorded_mock", msg_id=email.message_id, title=title)

        # Record notification to prevent duplicate future alerts
        await self.storage.record_sent_notification(
            notification_id=notification_id,
            message_id=email.message_id,
            title=title,
            task_id=task_id_str,
        )

        return NotificationDispatchResult(
            dispatched=True,
            status="delivered",
            notification_id=notification_id,
        )

