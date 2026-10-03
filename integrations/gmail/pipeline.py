"""End-to-end processing pipeline: Gmail Sync -> Content Normalization -> Local Qwen Analysis -> Task Extraction -> Notification."""

from __future__ import annotations

import asyncio
from typing import Any

from core.email_analysis.analyzer import EmailAnalyzer
from core.email_analysis.schemas import EmailAnalysisResult, TaskProposal
from core.email_analysis.task_extractor import TaskExtractor
from core.logging.setup import get_logger
from core.notifications.email_notifier import EmailNotificationService
from integrations.gmail.models import NormalizedEmail, SyncResult
from integrations.gmail.storage import EmailStorage
from integrations.gmail.sync import GmailSyncService

logger = get_logger("jarvis.gmail.pipeline")


class GmailPipelineResult:
    """Summary of complete email processing pipeline execution."""

    def __init__(self) -> None:
        self.sync_result: SyncResult = SyncResult()
        self.analyzed_count: int = 0
        self.tasks_extracted: int = 0
        self.notifications_sent: int = 0
        self.analyses: list[EmailAnalysisResult] = []
        self.tasks: list[TaskProposal] = []
        self.errors: list[str] = []


class GmailProcessingPipeline:
    """Coordinates full email intake: Sync -> Normalize -> Local AI Analysis -> Task Extraction -> Notification."""

    def __init__(
        self,
        sync_service: GmailSyncService,
        analyzer: EmailAnalyzer,
        task_extractor: TaskExtractor,
        notifier: EmailNotificationService,
        storage: EmailStorage,
    ) -> None:
        self.sync_service = sync_service
        self.analyzer = analyzer
        self.task_extractor = task_extractor
        self.notifier = notifier
        self.storage = storage

    async def run(self, account: str = "default") -> GmailPipelineResult:
        """Execute end-to-end email synchronization and analysis pipeline."""
        result = GmailPipelineResult()
        logger.info("gmail_pipeline_run_started", account=account)

        # 1. Synchronize new emails
        sync_res, new_emails = await self.sync_service.sync(account=account)
        result.sync_result = sync_res
        result.errors.extend(sync_res.errors)

        # Recover work stored before a crash or a failed analysis.
        seen = {email.message_id for email in new_emails}
        for message_id in await self.storage.pending_analysis_ids():
            if message_id not in seen:
                email = await self.storage.get_email(message_id)
                if email:
                    new_emails.append(email)

        if not new_emails:
            logger.info("gmail_pipeline_no_new_emails")
            return result

        logger.info("gmail_pipeline_processing_new_emails", count=len(new_emails))

        # 2. Process each newly received email sequentially with local AI
        for email in new_emails:
            try:
                # AI Analysis
                analysis = await self.analyzer.analyze(email)
                result.analyses.append(analysis)
                result.analyzed_count += 1

                # Task Extraction
                task = self.task_extractor.extract_task_proposal(email, analysis)
                if task:
                    await self.storage.save_task_proposal(task.model_dump())
                    result.tasks.append(task)
                    result.tasks_extracted += 1

                # Smart Notification
                notified = await self.notifier.notify_if_important(email, analysis, task)
                if notified:
                    result.notifications_sent += 1
                elif notified.status in ("permission_unavailable", "bridge_failed"):
                    result.errors.append(f"Notification unavailable: {notified.status}")
                    continue
                await self.storage.mark_analyzed(email.message_id)

            except Exception as proc_exc:
                logger.error("gmail_pipeline_item_failed", msg_id=email.message_id, error_type=type(proc_exc).__name__)
                result.errors.append(f"Email {email.message_id} analysis failed: {type(proc_exc).__name__}")

        logger.info(
            "gmail_pipeline_completed",
            new_synced=sync_res.new_count,
            analyzed=result.analyzed_count,
            tasks=result.tasks_extracted,
            notifications=result.notifications_sent,
        )
        return result
