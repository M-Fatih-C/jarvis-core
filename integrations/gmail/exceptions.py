"""Custom exceptions for Gmail integration and OAuth flow."""


class GmailIntegrationError(Exception):
    """Base exception for all Gmail integration errors."""
    pass


class GmailAuthError(GmailIntegrationError):
    """Raised when authentication, token validation, or OAuth flow fails."""
    pass


class GmailTokenExpiredError(GmailAuthError):
    """Raised when access token is expired and refresh token is unavailable or revoked."""
    pass


class GmailRevokedError(GmailAuthError):
    """Raised when user has revoked authorization for the application."""
    pass


class GmailQuotaExceededError(GmailIntegrationError):
    """Raised when Gmail API rate limits or quota boundaries are reached (HTTP 429)."""
    pass


class GmailNetworkError(GmailIntegrationError):
    """Raised when connection to Google API fails due to network outage or timeout."""
    pass


class GmailMessageNotFoundError(GmailIntegrationError):
    """Raised when an email message has been deleted or is inaccessible."""
    pass


class GmailHistoryExpiredError(GmailIntegrationError):
    """Raised when startHistoryId is out of date or expired (HTTP 404), requiring full lookback reinitialization."""
    pass

