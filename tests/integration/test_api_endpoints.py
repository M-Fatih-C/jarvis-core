"""Integration tests for FastAPI endpoints: /health, /v1/chat, and /v1/approvals."""

import pytest
from httpx import ASGITransport, AsyncClient
from api.dependencies import set_custom_llm_adapter, set_custom_runtime
from api.main import app
from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.runtime import AgentRuntime
from core.config.settings import Settings
from core.llm.mock_adapter import MockLLMAdapter
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.mock import register_mock_tools
from core.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def setup_api_env():
    """Setup mock adapter and isolated runtime for API tests."""
    registry = ToolRegistry()
    register_mock_tools(registry)
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)
    llm = MockLLMAdapter()
    runtime = AgentRuntime(
        llm_adapter=llm,
        tool_registry=registry,
        policy_engine=policy,
        tool_executor=executor,
        approval_store=ApprovalStore(),
        context_builder=ContextBuilder(),
        settings=Settings(),
    )
    set_custom_llm_adapter(llm)
    set_custom_runtime(runtime)
    yield
    set_custom_llm_adapter(None)
    set_custom_runtime(None)


@pytest.mark.asyncio
async def test_api_health_endpoint() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["agent"] == "ready"


@pytest.mark.asyncio
async def test_api_chat_direct_answer() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        resp = await client.post("/v1/chat", json={"message": "Merhaba Jarvis."})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["approval"] is None
        assert len(data["message"]) > 0


@pytest.mark.asyncio
async def test_api_chat_and_approval_flow() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
        # 1. Post chat requiring approval
        resp = await client.post(
            "/v1/chat",
            json={"message": "Yarın saat 19:00'da YBS çalışmayı hatırlat."},
        )
        assert resp.status_code == 200
        chat_data = resp.json()
        assert chat_data["status"] == "waiting_approval"
        assert chat_data["approval"] is not None
        assert chat_data["approval"]["tool"] == "reminders.create"

        approval_id = chat_data["approval"]["id"]

        # 2. Approve via endpoint
        appr_resp = await client.post(f"/v1/approvals/{approval_id}/approve")
        assert appr_resp.status_code == 200
        appr_data = appr_resp.json()
        assert appr_data["status"] == "completed"
        assert "oluşturuldu" in appr_data["message"]
