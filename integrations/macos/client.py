"""Unix Domain Socket IPC client for communicating with native JarvisMacAgent."""

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from core.logging.setup import get_logger
from integrations.macos.exceptions import (
    EventKitBridgeError,
    MacAgentProtocolError,
    MacAgentTimeoutError,
    MacAgentUnavailableError,
    MacBridgeError,
    PermissionDeniedBridgeError,
)
from integrations.macos.models import CURRENT_PROTOCOL_VERSION, RPCRequest, RPCResponse

logger = get_logger("jarvis.mac_bridge")

ALLOWED_METHODS = frozenset({
    "system.health",
    "calendar.list_calendars",
    "calendar.list_events",
    "calendar.get_event",
    "calendar.create_event",
    "calendar.update_event",
    "calendar.delete_event",
    "reminders.list_lists",
    "reminders.list",
    "reminders.create",
    "reminders.update",
    "reminders.complete",
    "reminders.delete",
    "notifications.status",
    "notifications.show",
    "notifications.schedule",
    "notifications.cancel",
})


def get_default_socket_path() -> str:
    """Return default secure user-isolated socket path."""
    uid = os.getuid()
    return f"/tmp/jarvis-{uid}/mac-agent.sock"


class MacBridgeClient:
    """Async client managing Unix Domain Socket IPC with JarvisMacAgent."""

    def __init__(
        self,
        socket_path: str | None = None,
        connect_timeout: float = 3.0,
        request_timeout: float = 10.0,
    ) -> None:
        self._socket_path = socket_path or get_default_socket_path()
        self._connect_timeout = connect_timeout
        self._request_timeout = request_timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    @property
    def socket_path(self) -> str:
        return self._socket_path

    async def _ensure_connected(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Establish or verify socket connection."""
        if self._reader is not None and self._writer is not None:
            if not self._writer.is_closing():
                return self._reader, self._writer

        # Validate socket file presence
        socket_file = Path(self._socket_path)
        if not socket_file.exists():
            raise MacAgentUnavailableError(
                f"JarvisMacAgent socket not found at '{self._socket_path}'. "
                "Ensure JarvisMacAgent is running."
            )

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self._socket_path),
                timeout=self._connect_timeout,
            )
            self._reader = reader
            self._writer = writer
            logger.info("mac_agent_socket_connected", path=self._socket_path)
            return self._reader, self._writer
        except asyncio.TimeoutError as exc:
            raise MacAgentTimeoutError(
                f"Connection to JarvisMacAgent at '{self._socket_path}' timed out."
            ) from exc
        except (ConnectionRefusedError, FileNotFoundError, OSError) as exc:
            raise MacAgentUnavailableError(
                f"Failed to connect to JarvisMacAgent at '{self._socket_path}': {exc}"
            ) from exc

    async def close(self) -> None:
        """Close active socket connection."""
        async with self._lock:
            await self._close_internal()

    async def _close_internal(self) -> None:
        """Helper to reset connection state on socket failures."""
        if self._writer is not None:
            try:
                self._writer.close()
                await asyncio.wait_for(self._writer.wait_closed(), timeout=0.5)
            except Exception:
                pass
            finally:
                self._writer = None
                self._reader = None

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Invoke an RPC method on JarvisMacAgent via Unix Domain Socket.
        
        Args:
            method: Registered RPC method name.
            params: Optional parameter dictionary.
            timeout: Optional call timeout in seconds.
            
        Returns:
            Result object on success.
            
        Raises:
            MacBridgeError: If method is not allowed.
            MacAgentUnavailableError: If agent is not running.
            MacAgentTimeoutError: If request or connection times out.
            MacAgentProtocolError: If response framing or protocol version is invalid.
            PermissionDeniedBridgeError: If macOS permission is denied.
            EventKitBridgeError: If native EventKit operation fails.
        """
        if method not in ALLOWED_METHODS:
            raise MacBridgeError(
                f"Method '{method}' is not permitted across MacBridge. "
                f"Allowed methods: {sorted(ALLOWED_METHODS)}"
            )

        request_params = params or {}
        req = RPCRequest(method=method, params=request_params)
        req_line = req.model_dump_json() + "\n"
        call_timeout = timeout or self._request_timeout

        async with self._lock:
            for attempt in range(2):
                try:
                    reader, writer = await self._ensure_connected()
                    writer.write(req_line.encode("utf-8"))
                    await writer.drain()

                    line = await asyncio.wait_for(
                        reader.readline(),
                        timeout=call_timeout,
                    )
                    if not line:
                        # EOF received, server closed connection
                        await self._close_internal()
                        if attempt == 0:
                            logger.warning("mac_agent_eof_retrying", method=method)
                            continue
                        raise MacAgentUnavailableError("Connection closed unexpectedly by JarvisMacAgent.")

                    return self._process_response(line, req.id)

                except asyncio.TimeoutError as exc:
                    await self._close_internal()
                    raise MacAgentTimeoutError(
                        f"RPC call '{method}' to JarvisMacAgent timed out after {call_timeout}s."
                    ) from exc
                except (ConnectionResetError, BrokenPipeError) as exc:
                    await self._close_internal()
                    if attempt == 0:
                        logger.warning("mac_agent_disconnected_retrying", method=method, error=str(exc))
                        continue
                    raise MacAgentUnavailableError(
                        f"Connection to JarvisMacAgent lost: {exc}"
                    ) from exc

    def _process_response(self, raw_line: bytes, expected_id: str) -> Any:
        """Parse NDJSON response and translate native status/errors."""
        try:
            data = json.loads(raw_line.decode("utf-8").strip())
            resp = RPCResponse.model_validate(data)
        except Exception as exc:
            raise MacAgentProtocolError(
                f"Invalid JSON/RPC response from JarvisMacAgent: {exc}. Payload: {raw_line[:100]!r}"
            ) from exc

        if resp.protocol_version != CURRENT_PROTOCOL_VERSION:
            raise MacAgentProtocolError(
                f"Protocol version mismatch: expected {CURRENT_PROTOCOL_VERSION}, got {resp.protocol_version}"
            )

        if resp.id != expected_id:
            raise MacAgentProtocolError(
                f"Response ID mismatch: expected {expected_id}, got {resp.id}"
            )

        if not resp.ok:
            err = resp.error
            code = err.code if err else "UNKNOWN_ERROR"
            message = err.message if err else "Unknown native error occurred."

            if code == "PERMISSION_DENIED":
                raise PermissionDeniedBridgeError(message)
            raise EventKitBridgeError(f"[{code}] {message}")

        return resp.result

    async def __aenter__(self) -> "MacBridgeClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
