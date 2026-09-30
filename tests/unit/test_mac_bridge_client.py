"""Unit tests for MacBridgeClient over Unix Domain Sockets with mock server (Milestone 3, Spec item 28)."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any
import pytest
from integrations.macos.client import MacBridgeClient
from integrations.macos.exceptions import (
    EventKitBridgeError,
    MacAgentProtocolError,
    MacAgentTimeoutError,
    MacAgentUnavailableError,
    MacBridgeError,
    PermissionDeniedBridgeError,
)
from integrations.macos.health import check_mac_agent_health


@pytest.fixture
def socket_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.mark.asyncio
async def test_disallowed_method_fails_immediately(socket_dir: Path) -> None:
    client = MacBridgeClient(socket_path=str(socket_dir / "test.sock"))
    with pytest.raises(MacBridgeError, match="Method 'arbitrary.dangerous_exec' is not permitted"):
        await client.call("arbitrary.dangerous_exec", {})


@pytest.mark.asyncio
async def test_socket_unavailable_raises(socket_dir: Path) -> None:
    non_existent = socket_dir / "does_not_exist.sock"
    client = MacBridgeClient(socket_path=str(non_existent))
    with pytest.raises(MacAgentUnavailableError, match="socket not found"):
        await client.call("system.health")


@pytest.mark.asyncio
async def test_successful_rpc_roundtrip(socket_dir: Path) -> None:
    sock_path = str(socket_dir / "mock.sock")

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        req = json.loads(line.decode("utf-8"))
        resp = {
            "protocol_version": 1,
            "id": req["id"],
            "ok": True,
            "result": {"calendars": [{"id": "cal-1", "title": "Personal"}]},
            "error": None,
        }
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle_client, path=sock_path)
    try:
        client = MacBridgeClient(socket_path=sock_path)
        res = await client.call("calendar.list_calendars")
        assert res == {"calendars": [{"id": "cal-1", "title": "Personal"}]}
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_permission_denied_mapping(socket_dir: Path) -> None:
    sock_path = str(socket_dir / "mock.sock")

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        req = json.loads(line.decode("utf-8"))
        resp = {
            "protocol_version": 1,
            "id": req["id"],
            "ok": False,
            "result": None,
            "error": {"code": "PERMISSION_DENIED", "message": "Calendar access denied by user."},
        }
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle_client, path=sock_path)
    try:
        client = MacBridgeClient(socket_path=sock_path)
        with pytest.raises(PermissionDeniedBridgeError, match="Calendar access denied by user."):
            await client.call("calendar.list_events", {"start": "2026-10-01T00:00:00+03:00"})
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_native_error_mapping(socket_dir: Path) -> None:
    sock_path = str(socket_dir / "mock.sock")

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        req = json.loads(line.decode("utf-8"))
        resp = {
            "protocol_version": 1,
            "id": req["id"],
            "ok": False,
            "result": None,
            "error": {"code": "EVENT_SAVE_FAILED", "message": "EventKit failed to write event."},
        }
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle_client, path=sock_path)
    try:
        client = MacBridgeClient(socket_path=sock_path)
        with pytest.raises(EventKitBridgeError, match=r"\[EVENT_SAVE_FAILED\] EventKit failed to write event."):
            await client.call("calendar.create_event", {"title": "Test Event"})
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_protocol_version_mismatch(socket_dir: Path) -> None:
    sock_path = str(socket_dir / "mock.sock")

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        req = json.loads(line.decode("utf-8"))
        resp = {
            "protocol_version": 99,
            "id": req["id"],
            "ok": True,
            "result": {},
            "error": None,
        }
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle_client, path=sock_path)
    try:
        client = MacBridgeClient(socket_path=sock_path)
        with pytest.raises(MacAgentProtocolError, match="Protocol version mismatch: expected 1, got 99"):
            await client.call("system.health")
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_request_id_mismatch(socket_dir: Path) -> None:
    sock_path = str(socket_dir / "mock.sock")

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        resp = {
            "protocol_version": 1,
            "id": "unrelated-uuid-456",
            "ok": True,
            "result": {},
            "error": None,
        }
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle_client, path=sock_path)
    try:
        client = MacBridgeClient(socket_path=sock_path)
        with pytest.raises(MacAgentProtocolError, match="Response ID mismatch"):
            await client.call("system.health")
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_request_timeout(socket_dir: Path) -> None:
    sock_path = str(socket_dir / "mock.sock")

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readline()
            await asyncio.sleep(1.0)
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_unix_server(handle_client, path=sock_path)
    client = MacBridgeClient(socket_path=sock_path, request_timeout=0.1)
    try:
        with pytest.raises(MacAgentTimeoutError, match="timed out"):
            await client.call("system.health")
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_health_check_connected(socket_dir: Path) -> None:
    sock_path = str(socket_dir / "mock.sock")

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        req = json.loads(line.decode("utf-8"))
        resp = {
            "protocol_version": 1,
            "id": req["id"],
            "ok": True,
            "result": {
                "agent": "ready",
                "protocol_version": 1,
                "calendar_permission": "full_access",
                "reminders_permission": "full_access",
                "notification_permission": "authorized",
            },
            "error": None,
        }
        writer.write((json.dumps(resp) + "\n").encode("utf-8"))
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle_client, path=sock_path)
    try:
        client = MacBridgeClient(socket_path=sock_path)
        health = await check_mac_agent_health(client)
        assert health["connected"] is True
        assert health["calendar_permission"] == "full_access"
        assert health["reminders_permission"] == "full_access"
        assert health["notification_permission"] == "authorized"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_health_check_disconnected(socket_dir: Path) -> None:
    client = MacBridgeClient(socket_path=str(socket_dir / "not_there.sock"))
    health = await check_mac_agent_health(client)
    assert health["connected"] is False
    assert "error" in health
