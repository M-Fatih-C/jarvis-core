"""Integration tests for Native macOS tools, PolicyEngine, ApprovalFlow, and MacBridge (Milestone 3, Spec items 25, 28, 32-34)."""

import asyncio
import json
from pathlib import Path
import tempfile
from typing import Any
import pytest
from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.exceptions import ApprovalIntegrityError
from core.agent.runtime import AgentRuntime
from core.agent.state_machine import AgentState
from core.config.settings import Settings
from core.llm.mock_adapter import MockLLMAdapter
from core.models.messages import ChatMessage, MessageRole
from core.models.tools import ToolCall
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.native_macos.provider import register_apple_tools
from core.tools.registry import ToolRegistry
from integrations.macos.client import MacBridgeClient


@pytest.fixture
def socket_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
async def mock_mac_agent_server(socket_dir: Path):
    sock_path = str(socket_dir / "mac-agent.sock")
    created_events = {}
    created_reminders = {}
    client_writers = set()

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client_writers.add(writer)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                req = json.loads(line.decode("utf-8"))
                method = req["method"]
                params = req.get("params", {})
                req_id = req["id"]

                if method == "system.health":
                    resp = {
                        "protocol_version": 1,
                        "id": req_id,
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
                elif method == "calendar.list_events":
                    resp = {
                        "protocol_version": 1,
                        "id": req_id,
                        "ok": True,
                        "result": {
                            "events": [
                                {
                                    "id": "ev-100",
                                    "calendar_id": "cal-1",
                                    "title": "Existing Meeting",
                                    "start": "2026-10-01T10:00:00+03:00",
                                    "end": "2026-10-01T11:00:00+03:00",
                                    "all_day": False,
                                    "availability": "busy",
                                }
                            ]
                        },
                        "error": None,
                    }
                elif method == "calendar.create_event":
                    ev_id = f"ev-{len(created_events) + 1}"
                    created_events[ev_id] = params
                    resp = {
                        "protocol_version": 1,
                        "id": req_id,
                        "ok": True,
                        "result": {
                            "id": ev_id,
                            "title": params.get("title"),
                            "start": params.get("start"),
                            "end": params.get("end"),
                        },
                        "error": None,
                    }
                elif method == "reminders.create":
                    rem_id = f"rem-{len(created_reminders) + 1}"
                    created_reminders[rem_id] = params
                    resp = {
                        "protocol_version": 1,
                        "id": req_id,
                        "ok": True,
                        "result": {
                            "id": rem_id,
                            "title": params.get("title"),
                            "due_at": params.get("due_at"),
                            "completed": False,
                        },
                        "error": None,
                    }
                elif method == "notifications.show":
                    resp = {
                        "protocol_version": 1,
                        "id": req_id,
                        "ok": True,
                        "result": {
                            "delivered": True,
                            "identifier": params.get("identifier") or "notif-1",
                            "title": params.get("title"),
                            "body": params.get("body"),
                        },
                        "error": None,
                    }
                else:
                    resp = {
                        "protocol_version": 1,
                        "id": req_id,
                        "ok": False,
                        "result": None,
                        "error": {"code": "METHOD_NOT_FOUND", "message": f"Method {method} not found"},
                    }

                writer.write((json.dumps(resp) + "\n").encode("utf-8"))
                await writer.drain()
        except Exception:
            pass
        finally:
            client_writers.discard(writer)
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_unix_server(handle_client, path=sock_path)
    try:
        yield sock_path
    finally:
        for w in list(client_writers):
            try:
                w.close()
            except Exception:
                pass
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_native_calendar_e2e_approval_and_execution(mock_mac_agent_server: str) -> None:
    """Scenario 32: Finding free slot and creating calendar event via native tool with R2 approval."""
    bridge = MacBridgeClient(socket_path=mock_mac_agent_server)
    registry = ToolRegistry()
    register_apple_tools(registry, provider="native_macos", client=bridge)
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)
    approval_store = ApprovalStore()
    settings = Settings(
        max_agent_steps=12,
        max_tool_calls=8,
        default_agent_mode="assist",
        apple_integration_provider="native_macos",
    )

    # 1. First step: list_events (R0 auto-executes)
    # 2. Second step: create_event (R2 requires approval)
    # 3. Third step: final response
    llm = MockLLMAdapter(responses=[
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Takviminizi kontrol ediyorum.",
            tool_calls=[
                ToolCall(
                    id="call_list_1",
                    name="calendar.list_events",
                    arguments={
                        "start": "2026-10-01T00:00:00+03:00",
                        "end": "2026-10-01T23:59:59+03:00",
                    },
                )
            ],
        ),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Yarın 15:00-17:00 arası boş görünüyorsunuz. Jarvis projesi için bu saati ayırayım mı?",
            tool_calls=[
                ToolCall(
                    id="call_create_1",
                    name="calendar.create_event",
                    arguments={
                        "title": "Jarvis Projesi",
                        "start": "2026-10-01T15:00:00+03:00",
                        "end": "2026-10-01T17:00:00+03:00",
                    },
                )
            ],
        ),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Jarvis projesi için yarın 15:00-17:00 arasına takvim etkinliğiniz başarıyla oluşturuldu.",
        ),
    ])

    runtime = AgentRuntime(
        llm_adapter=llm,
        tool_registry=registry,
        policy_engine=policy,
        tool_executor=executor,
        approval_store=approval_store,
        context_builder=ContextBuilder(),
        settings=settings,
    )

    # Run agent
    run = await runtime.run("Yarın akşam takvimimde boş olduğum iki saatlik bir zamanı bul ve Jarvis projesi için ayır.", source="chat")

    # Verify agent paused at create_event (R2 Write)
    assert run.state == AgentState.WAITING_APPROVAL
    assert run.pending_approval_id is not None
    assert run.pending_tool_call.name == "calendar.create_event"

    approval_id = run.pending_approval_id

    # Verify approval integrity record
    req = approval_store.get(approval_id)
    assert req.action_digest is not None
    assert req.consumed_at is None

    # User approves
    completed_run = await runtime.resume_approval(approval_id)
    assert completed_run.state == AgentState.COMPLETED
    assert "oluşturuldu" in completed_run.final_response

    # Verify approval record consumed
    consumed_req = approval_store.get(approval_id)
    assert consumed_req.consumed_at is not None

    await bridge.close()


@pytest.mark.asyncio
async def test_native_reminder_e2e_approval(mock_mac_agent_server: str) -> None:
    """Scenario 33: Creating reminder with R2 approval and verification."""
    bridge = MacBridgeClient(socket_path=mock_mac_agent_server)
    registry = ToolRegistry()
    register_apple_tools(registry, provider="native_macos", client=bridge)
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)
    approval_store = ApprovalStore()
    settings = Settings(
        max_agent_steps=12,
        max_tool_calls=8,
        default_agent_mode="assist",
        apple_integration_provider="native_macos",
    )

    llm = MockLLMAdapter(responses=[
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Hatırlatıcı oluşturuyorum.",
            tool_calls=[
                ToolCall(
                    id="call_rem_1",
                    name="reminders.create",
                    arguments={
                        "title": "YBS çalış",
                        "due_at": "2026-10-01T19:00:00+03:00",
                    },
                )
            ],
        ),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Yarın saat 19:00 için YBS çalışma hatırlatmanız oluşturuldu.",
        ),
    ])

    runtime = AgentRuntime(
        llm_adapter=llm,
        tool_registry=registry,
        policy_engine=policy,
        tool_executor=executor,
        approval_store=approval_store,
        context_builder=ContextBuilder(),
        settings=settings,
    )

    run = await runtime.run("Yarın saat 19:00'da YBS çalışmayı hatırlat.", source="chat")
    assert run.state == AgentState.WAITING_APPROVAL

    completed_run = await runtime.resume_approval(run.pending_approval_id)
    assert completed_run.state == AgentState.COMPLETED
    assert completed_run.tool_call_count == 1
    assert "oluşturuldu" in completed_run.final_response

    await bridge.close()


@pytest.mark.asyncio
async def test_native_notification_e2e_r1_auto_execute(mock_mac_agent_server: str) -> None:
    """Scenario 34: Local notification show auto-executes under R1_LOCAL_LOW policy."""
    bridge = MacBridgeClient(socket_path=mock_mac_agent_server)
    registry = ToolRegistry()
    register_apple_tools(registry, provider="native_macos", client=bridge)
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)
    approval_store = ApprovalStore()
    settings = Settings(
        max_agent_steps=12,
        max_tool_calls=8,
        default_agent_mode="assist",
        apple_integration_provider="native_macos",
    )

    llm = MockLLMAdapter(responses=[
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Bildirimi gönderiyorum.",
            tool_calls=[
                ToolCall(
                    id="call_notif_1",
                    name="notifications.show",
                    arguments={
                        "title": "Jarvis",
                        "body": "Test bildirimi",
                    },
                )
            ],
        ),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Bildirim gönderildi.",
        ),
    ])

    runtime = AgentRuntime(
        llm_adapter=llm,
        tool_registry=registry,
        policy_engine=policy,
        tool_executor=executor,
        approval_store=approval_store,
        context_builder=ContextBuilder(),
        settings=settings,
    )

    run = await runtime.run("Bana şimdi test bildirimi gönder.", source="chat")
    # R1 auto-executes without pausing in WAITING_APPROVAL
    assert run.state == AgentState.COMPLETED
    assert run.tool_call_count == 1
    assert "gönderildi" in run.final_response

    await bridge.close()


@pytest.mark.asyncio
async def test_permission_denial_graceful_explanation(socket_dir: Path) -> None:
    """Spec item 4 & 25: When permission is denied, native error PERMISSION_DENIED causes ToolResult failure, Qwen explains limitation."""
    sock_path = str(socket_dir / "denied-agent.sock")

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            req = json.loads(line.decode("utf-8"))
            resp = {
                "protocol_version": 1,
                "id": req["id"],
                "ok": False,
                "result": None,
                "error": {
                    "code": "PERMISSION_DENIED",
                    "message": "Calendar access is denied.",
                },
            }
            writer.write((json.dumps(resp) + "\n").encode("utf-8"))
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    server = await asyncio.start_unix_server(handle_client, path=sock_path)
    bridge = MacBridgeClient(socket_path=sock_path)
    registry = ToolRegistry()
    register_apple_tools(registry, provider="native_macos", client=bridge)
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)
    approval_store = ApprovalStore()
    settings = Settings(
        max_agent_steps=12,
        max_tool_calls=8,
        default_agent_mode="assist",
        apple_integration_provider="native_macos",
    )

    llm = MockLLMAdapter(responses=[
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Takviminizi kontrol ediyorum.",
            tool_calls=[
                ToolCall(
                    id="call_denied_1",
                    name="calendar.list_events",
                    arguments={
                        "start": "2026-10-01T00:00:00+03:00",
                        "end": "2026-10-01T23:59:59+03:00",
                    },
                )
            ],
        ),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Apple Calendar erişim izniniz olmadığı için takviminizi görüntüleyemedim. Lütfen Sistem Ayarları'ndan Jarvis'e Takvim izni verin.",
        ),
    ])

    runtime = AgentRuntime(
        llm_adapter=llm,
        tool_registry=registry,
        policy_engine=policy,
        tool_executor=executor,
        approval_store=approval_store,
        context_builder=ContextBuilder(),
        settings=settings,
    )

    try:
        run = await runtime.run("Bugünkü takvimime bak.", source="chat")
        assert run.state == AgentState.COMPLETED
        assert run.tool_call_count == 1
        assert "erişim izniniz olmadığı için" in run.final_response
    finally:
        await bridge.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_agent_unavailable_graceful_handling(socket_dir: Path) -> None:
    """Spec item 25: When JarvisMacAgent is unavailable, tool execution fails gracefully and does not crash agent."""
    non_existent = str(socket_dir / "not_running.sock")
    bridge = MacBridgeClient(socket_path=non_existent)
    registry = ToolRegistry()
    register_apple_tools(registry, provider="native_macos", client=bridge)
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)
    approval_store = ApprovalStore()
    settings = Settings(
        max_agent_steps=12,
        max_tool_calls=8,
        default_agent_mode="assist",
        apple_integration_provider="native_macos",
    )

    llm = MockLLMAdapter(responses=[
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="Takvim kontrol ediliyor.",
            tool_calls=[
                ToolCall(
                    id="call_unavail_1",
                    name="calendar.list_events",
                    arguments={
                        "start": "2026-10-01T00:00:00+03:00",
                        "end": "2026-10-01T23:59:59+03:00",
                    },
                )
            ],
        ),
        ChatMessage(
            role=MessageRole.ASSISTANT,
            content="JarvisMacAgent uygulamasına ulaşılamadı. Lütfen uygulamanın açık olduğundan emin olun.",
        ),
    ])

    runtime = AgentRuntime(
        llm_adapter=llm,
        tool_registry=registry,
        policy_engine=policy,
        tool_executor=executor,
        approval_store=approval_store,
        context_builder=ContextBuilder(),
        settings=settings,
    )

    run = await runtime.run("Takvimime bak.", source="chat")
    assert run.state == AgentState.COMPLETED
    assert run.tool_call_count == 1
    assert "uygulamasına ulaşılamadı" in run.final_response
