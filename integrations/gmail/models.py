"""Pydantic data models for Gmail integration, OAuth tokens, and normalized email schemas."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import Any
from pydantic import BaseModel, Field


class OAuthTokens(BaseModel):
    """Google OAuth 2.0 token container."""

    access_token: str
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_in: int = 3600
    expires_at: float = Field(default_factory=lambda: datetime.now(timezone.utc).timestamp() + 3600)
    scope: str = "https://www.googleapis.com/auth/gmail.readonly"

    @property
    def is_expired(self) -> bool:
        """Return True if the token has expired or will expire in the next 60 seconds."""
        now = datetime.now(timezone.utc).timestamp()
        return now >= (self.expires_at - 60)


class GmailAccountProfile(BaseModel):
    """Gmail user account profile information."""

    email_address: str
    messages_total: int = 0
    threads_total: int = 0
    history_id: str | None = None


class AttachmentMetadata(BaseModel):
    """Metadata for an email attachment (content is NOT downloaded automatically)."""

    attachment_id: str = ""
    filename: str
    mime_type: str
    size_bytes: int = 0


class NormalizedEmail(BaseModel):
    """Clean, normalized representation of an email message."""

    message_id: str
    thread_id: str
    sender: str
    sender_email: str = ""
    recipient: str = ""
    subject: str = ""
    received_at: datetime
    body_plain: str = ""
    body_preview: str = ""
    attachments: list[AttachmentMetadata] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    content_hash: str = ""

    def compute_content_hash(self) -> str:
        """Compute SHA-256 hash of stable message attributes for deduplication."""
        hasher = hashlib.sha256()
        hasher.update(self.message_id.encode("utf-8"))
        hasher.update(self.subject.encode("utf-8"))
        hasher.update(self.body_plain.encode("utf-8", errors="replace"))
        return hasher.hexdigest()


class EmailSyncState(BaseModel):
    """Persistent synchronization state for an account."""

    account_email: str
    last_synced_at: datetime | None = None
    last_history_id: str | None = None
    total_messages_synced: int = 0
    sync_cursor_date: str | None = None
    status: str = "idle"  # idle, syncing, error
    error_message: str | None = None


class SyncResult(BaseModel):
    """Result of an email synchronization run."""

    synced_count: int = 0
    new_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    account_email: str = ""
    errors: list[str] = Field(default_factory=list)
