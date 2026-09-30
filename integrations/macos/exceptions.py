"""Exceptions for macOS Native Bridge and IPC communication."""

from core.agent.exceptions import JarvisError


class MacBridgeError(JarvisError):
    """Base exception for all macOS Bridge and IPC errors."""
    pass


class MacAgentUnavailableError(MacBridgeError):
    """Raised when JarvisMacAgent is not running or the Unix domain socket cannot be connected."""
    pass


class MacAgentTimeoutError(MacBridgeError):
    """Raised when connection or IPC call to JarvisMacAgent times out."""
    pass


class MacAgentProtocolError(MacBridgeError):
    """Raised when IPC message framing, protocol version, or response format is invalid."""
    pass


class PermissionDeniedBridgeError(MacBridgeError):
    """Raised when macOS privacy permissions (Calendar, Reminders, Notifications) are denied."""
    pass


class EventKitBridgeError(MacBridgeError):
    """Raised when native EventKit operations fail (e.g. save error, not found, recurrence error)."""
    pass
