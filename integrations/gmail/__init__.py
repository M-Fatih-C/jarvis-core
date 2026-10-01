"""Gmail integration package for Jarvis."""

from integrations.gmail.auth import GmailOAuthManager, KeychainTokenStore
from integrations.gmail.client import GmailClient
from integrations.gmail.exceptions import (
    GmailAuthError,
    GmailIntegrationError,
    GmailMessageNotFoundError,
    GmailNetworkError,
    GmailQuotaExceededError,
    GmailRevokedError,
    GmailTokenExpiredError,
)
from integrations.gmail.models import (
    AttachmentMetadata,
    EmailSyncState,
    GmailAccountProfile,
    NormalizedEmail,
    OAuthTokens,
    SyncResult,
)
from integrations.gmail.normalizer import GmailNormalizer
from integrations.gmail.storage import EmailStorage
from integrations.gmail.sync import GmailSyncService

__all__ = [
    "AttachmentMetadata",
    "EmailStorage",
    "EmailSyncState",
    "GmailAccountProfile",
    "GmailAuthError",
    "GmailClient",
    "GmailIntegrationError",
    "GmailMessageNotFoundError",
    "GmailNetworkError",
    "GmailNormalizer",
    "GmailOAuthManager",
    "GmailQuotaExceededError",
    "GmailRevokedError",
    "GmailSyncService",
    "GmailTokenExpiredError",
    "KeychainTokenStore",
    "NormalizedEmail",
    "OAuthTokens",
    "SyncResult",
]
