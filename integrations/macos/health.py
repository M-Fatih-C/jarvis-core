"""Health checking utilities for native macOS integration."""

from typing import Any
from integrations.macos.client import MacBridgeClient
from integrations.macos.exceptions import MacBridgeError


async def check_mac_agent_health(client: MacBridgeClient) -> dict[str, Any]:
    """Check health and permission status of the native JarvisMacAgent.
    
    Returns:
        Structured health status dictionary without exposing sensitive calendar/reminder content.
    """
    try:
        res = await client.call("system.health", timeout=2.0)
        return {
            "connected": True,
            "agent": res.get("agent", "ready"),
            "protocol_version": res.get("protocol_version", 1),
            "calendar_permission": res.get("calendar_permission", "unknown"),
            "reminders_permission": res.get("reminders_permission", "unknown"),
            "notification_permission": res.get("notification_permission", "unknown"),
        }
    except MacBridgeError as exc:
        return {
            "connected": False,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "connected": False,
            "error": f"Unexpected health check failure: {exc}",
        }
