"""Local SQLite persistent storage for Gmail synchronization state, messages, analyses, and tasks."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any
from uuid import UUID

from core.logging.setup import get_logger
from core.memory.crypto import KeyProvider, MacKeychainKeyProvider, MemoryEncryptor
from core.task_planning.schemas import (
    ActionDestination,
    ActionType,
    CalendarCategory,
    TaskActionProposal,
)
from core.task_planning.state import TaskProposalStatus
from integrations.gmail.models import (
    AttachmentMetadata,
    EmailSyncState,
    NormalizedEmail,
)

logger = get_logger("jarvis.gmail.storage")


class EmailStorage:
    """Thread-safe SQLite persistent store for email sync state, messages, and task proposals.
    
    Retained message bodies are encrypted at rest with AES-256-GCM using keys managed
    through macOS Keychain via MacKeychainKeyProvider.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        key_provider: KeyProvider | None = None,
        encryption_enabled: bool = True,
    ) -> None:
        if db_path != ":memory:":
            expanded_path = Path(os.path.expanduser(db_path)).resolve()
            expanded_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_path = str(expanded_path)
        else:
            self._db_path = ":memory:"

        self.encryption_enabled = encryption_enabled
        self.key_provider = key_provider or MacKeychainKeyProvider(
            service_name="com.jarvis.email",
            username="email_storage_key",
            fallback_to_memory=(self._db_path == ":memory:"),
        )
        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        """Create necessary tables and indices."""
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS processed_emails (message_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS email_monitoring_preferences (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    started_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS email_sync_state (
                    account_email TEXT PRIMARY KEY,
                    last_synced_at TEXT,
                    last_history_id TEXT,
                    total_messages_synced INTEGER DEFAULT 0,
                    sync_cursor_date TEXT,
                    status TEXT DEFAULT 'idle',
                    error_message TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS emails (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    account_email TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    sender_email TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    body_plain TEXT,
                    body_preview TEXT,
                    attachments_json TEXT,
                    headers_json TEXT,
                    labels_json TEXT,
                    content_hash TEXT NOT NULL,
                    synced_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_emails_account_received
                    ON emails(account_email, received_at DESC);
                CREATE INDEX IF NOT EXISTS idx_emails_content_hash
                    ON emails(content_hash);

                CREATE TABLE IF NOT EXISTS email_analyses (
                    message_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    requires_response INTEGER NOT NULL,
                    contains_task INTEGER NOT NULL,
                    has_deadline INTEGER NOT NULL,
                    has_meeting INTEGER NOT NULL,
                    security_or_payment INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    proposed_action TEXT NOT NULL,
                    raw_response_json TEXT,
                    analyzed_at TEXT NOT NULL,
                    FOREIGN KEY(message_id) REFERENCES emails(message_id)
                );

                CREATE TABLE IF NOT EXISTS task_proposals (
                    task_id TEXT PRIMARY KEY,
                    source_message_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    category TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    deadline TEXT,
                    deadline_confidence TEXT NOT NULL,
                    raw_deadline_text TEXT,
                    proposed_action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(source_message_id) REFERENCES emails(message_id)
                );

                CREATE INDEX IF NOT EXISTS idx_task_proposals_status
                    ON task_proposals(status, created_at DESC);

                CREATE TABLE IF NOT EXISTS sent_notifications (
                    notification_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    task_id TEXT,
                    title TEXT NOT NULL,
                    sent_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sent_notifications_msg
                    ON sent_notifications(message_id);

                CREATE TABLE IF NOT EXISTS task_actions (
                    action_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    target_destination TEXT NOT NULL,
                    title TEXT NOT NULL,
                    notes TEXT,
                    start_time TEXT,
                    end_time TEXT,
                    due_date TEXT,
                    target_calendar_id TEXT,
                    target_list_id TEXT,
                    logical_category TEXT,
                    approval_id TEXT,
                    action_digest TEXT,
                    status TEXT NOT NULL,
                    external_id TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    executed_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES task_proposals(task_id),
                    FOREIGN KEY(source_message_id) REFERENCES emails(message_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_task_actions_idempotency
                    ON task_actions(source_message_id, task_id, action_type);
                CREATE INDEX IF NOT EXISTS idx_task_actions_external_id
                    ON task_actions(external_id);
                CREATE INDEX IF NOT EXISTS idx_task_actions_task_id
                    ON task_actions(task_id);
            """)

    async def get_sync_state(self, account_email: str) -> EmailSyncState | None:
        """Fetch synchronization state for an account."""
        async with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM email_sync_state WHERE account_email = ?",
                (account_email,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            return EmailSyncState(
                account_email=row["account_email"],
                last_synced_at=datetime.fromisoformat(row["last_synced_at"]) if row["last_synced_at"] else None,
                last_history_id=row["last_history_id"],
                total_messages_synced=row["total_messages_synced"],
                sync_cursor_date=row["sync_cursor_date"],
                status=row["status"],
                error_message=row["error_message"],
            )

    async def save_sync_state(self, state: EmailSyncState) -> None:
        """Upsert synchronization state."""
        now_iso = datetime.now(timezone.utc).isoformat()
        last_synced = state.last_synced_at.isoformat() if state.last_synced_at else None

        async with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO email_sync_state (
                        account_email, last_synced_at, last_history_id,
                        total_messages_synced, sync_cursor_date, status,
                        error_message, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_email) DO UPDATE SET
                        last_synced_at = excluded.last_synced_at,
                        last_history_id = excluded.last_history_id,
                        total_messages_synced = excluded.total_messages_synced,
                        sync_cursor_date = excluded.sync_cursor_date,
                        status = excluded.status,
                        error_message = excluded.error_message,
                        updated_at = excluded.updated_at
                    """,
                    (
                        state.account_email,
                        last_synced,
                        state.last_history_id,
                        state.total_messages_synced,
                        state.sync_cursor_date,
                        state.status,
                        state.error_message,
                        now_iso,
                    ),
                )

    async def is_email_processed(self, message_id: str) -> bool:
        """Check if an email has already been stored."""
        async with self._lock:
            cursor = self._conn.execute(
                "SELECT 1 FROM emails WHERE message_id = ?",
                (message_id,),
            )
            return cursor.fetchone() is not None

    async def _decrypt_text(self, text: str | None) -> str:
        """Decrypt AES-256-GCM encrypted payload or return cleartext as fallback."""
        if not text or not self.encryption_enabled:
            return text or ""
        if text.startswith('{"ciphertext":'):
            try:
                key = await self.key_provider.get_or_create_memory_key()
                return MemoryEncryptor.decrypt(text, key)
            except Exception as exc:
                logger.warning("email_body_decrypt_failed", error=str(exc))
                return "[ENCRYPTED CONTENT]"
        return text

    async def save_email(self, email: NormalizedEmail, account_email: str) -> bool:
        """Save normalized email with AES-256-GCM encrypted body. Returns True if inserted."""
        if await self.is_email_processed(email.message_id):
            return False

        now_iso = datetime.now(timezone.utc).isoformat()
        att_json = json.dumps([a.model_dump() for a in email.attachments])
        headers_json = json.dumps(email.headers)
        labels_json = json.dumps(email.labels)

        # Encrypt body content at rest
        body_to_store = email.body_plain
        preview_to_store = email.body_preview
        if self.encryption_enabled:
            try:
                key = await self.key_provider.get_or_create_memory_key()
                if email.body_plain:
                    body_to_store = json.dumps(MemoryEncryptor.encrypt(email.body_plain, key))
                if email.body_preview:
                    preview_to_store = json.dumps(MemoryEncryptor.encrypt(email.body_preview, key))
            except Exception as exc:
                logger.warning("email_body_encrypt_failed", error_type=type(exc).__name__)
                raise

        async with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO emails (
                        message_id, thread_id, account_email, sender, sender_email,
                        recipient, subject, received_at, body_plain, body_preview,
                        attachments_json, headers_json, labels_json, content_hash, synced_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email.message_id,
                        email.thread_id,
                        account_email,
                        email.sender,
                        email.sender_email,
                        email.recipient,
                        email.subject,
                        email.received_at.isoformat(),
                        body_to_store,
                        preview_to_store,
                        att_json,
                        headers_json,
                        labels_json,
                        email.content_hash,
                        now_iso,
                    ),
                )
            return True

    async def get_email(self, message_id: str) -> NormalizedEmail | None:
        """Retrieve stored normalized email with decrypted body."""
        async with self._lock:
            cursor = self._conn.execute("SELECT * FROM emails WHERE message_id = ?", (message_id,))
            row = cursor.fetchone()
            if not row:
                return None

            attachments = [AttachmentMetadata(**a) for a in json.loads(row["attachments_json"] or "[]")]
            decrypted_body = await self._decrypt_text(row["body_plain"])
            decrypted_preview = await self._decrypt_text(row["body_preview"])

            return NormalizedEmail(
                message_id=row["message_id"],
                thread_id=row["thread_id"],
                sender=row["sender"],
                sender_email=row["sender_email"],
                recipient=row["recipient"],
                subject=row["subject"],
                received_at=datetime.fromisoformat(row["received_at"]),
                body_plain=decrypted_body,
                body_preview=decrypted_preview,
                attachments=attachments,
                headers=json.loads(row["headers_json"] or "{}"),
                labels=json.loads(row["labels_json"] or "[]"),
                content_hash=row["content_hash"],
            )

    async def get_recent_emails(self, account_email: str, limit: int = 50) -> list[NormalizedEmail]:
        """Fetch recently received emails with decrypted bodies."""
        async with self._lock:
            cursor = self._conn.execute(
                """
                SELECT * FROM emails
                WHERE account_email = ?
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (account_email, limit),
            )
            rows = cursor.fetchall()
            results: list[NormalizedEmail] = []
            for row in rows:
                attachments = [AttachmentMetadata(**a) for a in json.loads(row["attachments_json"] or "[]")]
                decrypted_body = await self._decrypt_text(row["body_plain"])
                decrypted_preview = await self._decrypt_text(row["body_preview"])
                results.append(
                    NormalizedEmail(
                        message_id=row["message_id"],
                        thread_id=row["thread_id"],
                        sender=row["sender"],
                        sender_email=row["sender_email"],
                        recipient=row["recipient"],
                        subject=row["subject"],
                        received_at=datetime.fromisoformat(row["received_at"]),
                        body_plain=decrypted_body,
                        body_preview=decrypted_preview,
                        attachments=attachments,
                        headers=json.loads(row["headers_json"] or "{}"),
                        labels=json.loads(row["labels_json"] or "[]"),
                        content_hash=row["content_hash"],
                    )
                )
            return results

    async def delete_email(self, message_id: str) -> bool:
        """Delete an email and related records from local storage."""
        async with self._lock:
            with self._conn:
                cursor = self._conn.execute("DELETE FROM emails WHERE message_id = ?", (message_id,))
                self._conn.execute("DELETE FROM email_analyses WHERE message_id = ?", (message_id,))
                self._conn.execute("DELETE FROM task_proposals WHERE source_message_id = ?", (message_id,))
                self._conn.execute("DELETE FROM sent_notifications WHERE message_id = ?", (message_id,))
                return cursor.rowcount > 0

    async def prune_retained_bodies(self, older_than_days: int = 14) -> int:
        """Clear body_plain for emails older than older_than_days while retaining metadata."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        cutoff_iso = cutoff.isoformat()
        async with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    "UPDATE emails SET body_plain = '' WHERE received_at < ? AND body_plain != ''",
                    (cutoff_iso,),
                )
                logger.info("pruned_old_email_bodies", count=cursor.rowcount, older_than_days=older_than_days)
                return cursor.rowcount

    async def clear_account_data(self, account_email: str) -> None:
        """Completely purge all cached emails, analyses, task proposals, and sync state for an account."""
        async with self._lock:
            with self._conn:
                self._conn.execute(
                    "DELETE FROM task_proposals WHERE source_message_id IN (SELECT message_id FROM emails WHERE account_email = ?)",
                    (account_email,),
                )
                self._conn.execute(
                    "DELETE FROM email_analyses WHERE message_id IN (SELECT message_id FROM emails WHERE account_email = ?)",
                    (account_email,),
                )
                self._conn.execute(
                    "DELETE FROM sent_notifications WHERE message_id IN (SELECT message_id FROM emails WHERE account_email = ?)",
                    (account_email,),
                )
                self._conn.execute("DELETE FROM emails WHERE account_email = ?", (account_email,))
                self._conn.execute("DELETE FROM email_sync_state WHERE account_email = ?", (account_email,))
                logger.info("cleared_account_data", account=account_email)


    async def save_task_proposal(self, proposal: dict[str, Any]) -> None:
        """Store an extracted task proposal."""
        deadline_val = proposal.get("deadline")
        deadline_str = deadline_val.isoformat() if isinstance(deadline_val, datetime) else (str(deadline_val) if deadline_val else None)
        created_val = proposal.get("created_at")
        created_str = created_val.isoformat() if isinstance(created_val, datetime) else str(created_val)

        def _raw(val: Any) -> str:
            return val.value if hasattr(val, "value") else str(val)

        async with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO task_proposals (
                        task_id, source_message_id, title, description, category,
                        priority, deadline, deadline_confidence, raw_deadline_text,
                        proposed_action, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(proposal["task_id"]),
                        proposal["source_message_id"],
                        proposal["title"],
                        proposal["description"],
                        _raw(proposal["category"]),
                        _raw(proposal["priority"]),
                        deadline_str,
                        _raw(proposal["deadline_confidence"]),
                        proposal.get("raw_deadline_text"),
                        proposal["proposed_action"],
                        _raw(proposal["status"]),
                        created_str,
                    ),
                )

    async def get_task_proposals(self, status: str | None = None) -> list[dict[str, Any]]:
        """Retrieve task proposals with optional status filter."""
        async with self._lock:
            if status:
                cursor = self._conn.execute(
                    "SELECT * FROM task_proposals WHERE UPPER(status) = UPPER(?) ORDER BY created_at DESC",
                    (status,),
                )
            else:
                cursor = self._conn.execute("SELECT * FROM task_proposals ORDER BY created_at DESC")

            rows = cursor.fetchall()
            results: list[dict[str, Any]] = []
            for r in rows:
                item = dict(r)
                if item.get("status"):
                    item["status"] = TaskProposalStatus.from_str(item["status"])
                results.append(item)
            return results

    async def get_task_proposal(self, task_id: str | UUID) -> dict[str, Any] | None:
        """Fetch a single task proposal by task_id."""
        async with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM task_proposals WHERE task_id = ?",
                (str(task_id),),
            )
            row = cursor.fetchone()
            if not row:
                return None
            item = dict(row)
            if item.get("status"):
                item["status"] = TaskProposalStatus.from_str(item["status"])
            return item

    async def update_task_proposal_status(
        self,
        task_id: str | UUID,
        status: TaskProposalStatus | str,
    ) -> None:
        """Update the lifecycle status of a task proposal."""
        status_str = status.value if hasattr(status, "value") else str(status)
        async with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE task_proposals SET status = ? WHERE task_id = ?",
                    (status_str, str(task_id)),
                )

    def _row_to_task_action(self, row: sqlite3.Row) -> TaskActionProposal:
        """Helper to convert sqlite3.Row to TaskActionProposal."""
        return TaskActionProposal(
            action_id=UUID(row["action_id"]),
            task_id=UUID(row["task_id"]),
            source_message_id=row["source_message_id"],
            action_type=ActionType(row["action_type"]),
            target_destination=ActionDestination(row["target_destination"]),
            title=row["title"],
            notes=row["notes"],
            start_time=datetime.fromisoformat(row["start_time"]) if row["start_time"] else None,
            end_time=datetime.fromisoformat(row["end_time"]) if row["end_time"] else None,
            due_date=datetime.fromisoformat(row["due_date"]) if row["due_date"] else None,
            target_calendar_id=row["target_calendar_id"],
            target_list_id=row["target_list_id"],
            logical_category=CalendarCategory(row["logical_category"]) if row["logical_category"] else CalendarCategory.WORK,
            approval_id=UUID(row["approval_id"]) if row["approval_id"] else None,
            action_digest=row["action_digest"],
            status=TaskProposalStatus.from_str(row["status"]),
            external_id=row["external_id"],
            error_message=row["error_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            executed_at=datetime.fromisoformat(row["executed_at"]) if row["executed_at"] else None,
        )

    async def save_task_action(self, action: TaskActionProposal) -> None:
        """Insert or replace a task action proposal enforcing idempotency."""
        now_iso = datetime.now(timezone.utc).isoformat()
        start_str = action.start_time.isoformat() if action.start_time else None
        end_str = action.end_time.isoformat() if action.end_time else None
        due_str = action.due_date.isoformat() if action.due_date else None
        created_str = action.created_at.isoformat() if action.created_at else now_iso
        executed_str = action.executed_at.isoformat() if action.executed_at else None

        async with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO task_actions (
                        action_id, task_id, source_message_id, action_type,
                        target_destination, title, notes, start_time, end_time,
                        due_date, target_calendar_id, target_list_id, logical_category,
                        approval_id, action_digest, status, external_id,
                        error_message, created_at, executed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_message_id, task_id, action_type) DO UPDATE SET
                        target_destination = excluded.target_destination,
                        title = excluded.title,
                        notes = excluded.notes,
                        start_time = excluded.start_time,
                        end_time = excluded.end_time,
                        due_date = excluded.due_date,
                        target_calendar_id = excluded.target_calendar_id,
                        target_list_id = excluded.target_list_id,
                        logical_category = excluded.logical_category,
                        approval_id = excluded.approval_id,
                        action_digest = excluded.action_digest,
                        status = excluded.status,
                        external_id = excluded.external_id,
                        error_message = excluded.error_message,
                        executed_at = excluded.executed_at
                    """,
                    (
                        str(action.action_id),
                        str(action.task_id),
                        action.source_message_id,
                        action.action_type.value,
                        action.target_destination.value,
                        action.title,
                        action.notes,
                        start_str,
                        end_str,
                        due_str,
                        action.target_calendar_id,
                        action.target_list_id,
                        action.logical_category.value,
                        str(action.approval_id) if action.approval_id else None,
                        action.action_digest,
                        action.status.value,
                        action.external_id,
                        action.error_message,
                        created_str,
                        executed_str,
                    ),
                )

    async def get_task_action(self, action_id: str | UUID) -> TaskActionProposal | None:
        """Fetch a specific task action by ID."""
        async with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM task_actions WHERE action_id = ?",
                (str(action_id),),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_task_action(row)

    async def get_task_action_by_idempotency_key(
        self,
        source_message_id: str,
        task_id: str | UUID,
        action_type: str,
    ) -> TaskActionProposal | None:
        """Fetch an action by its unique idempotency composite key."""
        async with self._lock:
            cursor = self._conn.execute(
                """
                SELECT * FROM task_actions
                WHERE source_message_id = ? AND task_id = ? AND action_type = ?
                """,
                (source_message_id, str(task_id), action_type),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_task_action(row)

    async def get_task_actions_for_task(self, task_id: str | UUID) -> list[TaskActionProposal]:
        """Fetch all planned actions associated with a task proposal."""
        async with self._lock:
            cursor = self._conn.execute(
                "SELECT * FROM task_actions WHERE task_id = ? ORDER BY created_at ASC",
                (str(task_id),),
            )
            rows = cursor.fetchall()
            return [self._row_to_task_action(r) for r in rows]

    async def update_task_action(self, action: TaskActionProposal) -> None:
        """Update fields of an existing task action proposal."""
        await self.save_task_action(action)

    async def get_source_email_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        """Find the original email and task proposal that generated an EventKit event or reminder."""
        async with self._lock:
            cursor = self._conn.execute(
                """
                SELECT 
                    a.action_id, a.task_id, a.source_message_id, a.action_type,
                    a.title as action_title, a.status as action_status, a.external_id, a.executed_at,
                    p.title as task_title, p.category as task_category, p.priority as task_priority,
                    e.subject, e.sender, e.sender_email, e.received_at, e.body_plain, e.body_preview
                FROM task_actions a
                JOIN task_proposals p ON a.task_id = p.task_id
                JOIN emails e ON a.source_message_id = e.message_id
                WHERE a.external_id = ?
                """,
                (external_id,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            decrypted_preview = await self._decrypt_text(row["body_preview"])
            decrypted_plain = await self._decrypt_text(row["body_plain"])

            return {
                "external_id": row["external_id"],
                "action_id": row["action_id"],
                "action_type": row["action_type"],
                "action_title": row["action_title"],
                "action_status": row["action_status"],
                "executed_at": row["executed_at"],
                "task_id": row["task_id"],
                "task_title": row["task_title"],
                "task_category": row["task_category"],
                "source_message_id": row["source_message_id"],
                "email_subject": row["subject"],
                "email_sender": row["sender"],
                "email_sender_email": row["sender_email"],
                "email_received_at": row["received_at"],
                "email_preview": decrypted_preview or (decrypted_plain[:200] if decrypted_plain else ""),
            }

    async def is_notification_sent(self, message_id: str) -> bool:
        """Check if notification has already been dispatched for this email."""
        async with self._lock:
            cursor = self._conn.execute(
                "SELECT 1 FROM sent_notifications WHERE message_id = ?",
                (message_id,),
            )
            return cursor.fetchone() is not None

    async def record_sent_notification(
        self,
        notification_id: str,
        message_id: str,
        title: str,
        task_id: str | None = None,
    ) -> None:
        """Record dispatched notification to prevent duplicate user alerts."""
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO sent_notifications (
                        notification_id, message_id, task_id, title, sent_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (notification_id, message_id, task_id, title, now_iso),
                )

    async def claim_notification(self, notification_id: str, message_id: str, title: str, task_id: str | None = None) -> bool:
        """Reserve once before dispatch; an uncertain delivery is never repeated."""
        async with self._lock:
            with self._conn:
                cursor = self._conn.execute(
                    "INSERT OR IGNORE INTO sent_notifications (notification_id, message_id, task_id, title, sent_at) VALUES (?, ?, ?, ?, ?)",
                    (notification_id, message_id, task_id, title, datetime.now(timezone.utc).isoformat()),
                )
                return cursor.rowcount == 1

    async def release_notification(self, notification_id: str) -> None:
        """Allow retry only when the native bridge explicitly rejected delivery."""
        async with self._lock:
            with self._conn:
                self._conn.execute("DELETE FROM sent_notifications WHERE notification_id = ?", (notification_id,))

    def close(self) -> None:
        """Close SQLite connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    async def pending_analysis_ids(self, limit: int = 100) -> list[str]:
        async with self._lock:
            return [r[0] for r in self._conn.execute(
                """SELECT message_id FROM emails
                   WHERE message_id NOT IN (SELECT message_id FROM processed_emails)
                   AND (NOT EXISTS (SELECT 1 FROM email_monitoring_preferences)
                        OR julianday(received_at) >= (SELECT julianday(started_at) FROM email_monitoring_preferences WHERE id=1))
                   ORDER BY received_at LIMIT ?""", (limit,)
            )]

    async def set_monitoring_start(self, started_at: datetime) -> None:
        """Persist an explicit new-mail-only boundary without deleting existing mail."""
        if started_at.tzinfo is None:
            raise ValueError("Monitoring start must include a timezone")
        async with self._lock:
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO email_monitoring_preferences VALUES (1, ?)",
                    (started_at.astimezone(timezone.utc).isoformat(),),
                )

    async def get_monitoring_start(self) -> datetime | None:
        async with self._lock:
            row = self._conn.execute("SELECT started_at FROM email_monitoring_preferences WHERE id=1").fetchone()
            return datetime.fromisoformat(row[0]) if row else None

    async def mark_analyzed(self, message_id: str) -> None:
        async with self._lock:
            with self._conn:
                self._conn.execute("INSERT OR IGNORE INTO processed_emails VALUES (?)", (message_id,))
