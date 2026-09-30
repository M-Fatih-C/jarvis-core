"""Native macOS integration package."""

from integrations.macos.client import MacBridgeClient, get_default_socket_path
from integrations.macos.exceptions import (
    EventKitBridgeError,
    MacAgentProtocolError,
    MacAgentTimeoutError,
    MacAgentUnavailableError,
    MacBridgeError,
    PermissionDeniedBridgeError,
)
from integrations.macos.health import check_mac_agent_health
from integrations.macos.models import CURRENT_PROTOCOL_VERSION, RPCError, RPCRequest, RPCResponse

__all__ = [
    "CURRENT_PROTOCOL_VERSION",
    "EventKitBridgeError",
    "MacAgentProtocolError",
    "MacAgentTimeoutError",
    "MacAgentUnavailableError",
    "MacBridgeClient",
    "MacBridgeError",
    "PermissionDeniedBridgeError",
    "RPCError",
    "RPCRequest",
    "RPCResponse",
    "check_mac_agent_health",
    "get_default_socket_path",
]
