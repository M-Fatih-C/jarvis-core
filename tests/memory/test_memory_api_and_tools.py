"""Tests for memory API endpoints and agent memory tools."""

import pytest
from httpx import ASGITransport, AsyncClient
from api.dependencies import set_custom_memory_service
from api.main import create_app
from core.memory.crypto import InMemoryKeyProvider
from core.memory.embeddings.mock import DeterministicMockEmbeddingProvider
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.models import MemoryKind, MemorySensitivity
from core.memory.service import MemoryService
from core.tools.memory_tools import (
    MemoryForgetTool,
    MemoryListTool,
    MemoryRememberTool,
    MemorySearchTool,
    MemoryUpdateTool,
)


@pytest.fixture
def memory_service() -> MemoryService:
    repo = SQLiteMemoryRepository(":memory:")
    embedder = DeterministicMockEmbeddingProvider(dimensions=384)
    key_prov = InMemoryKeyProvider()
    return MemoryService(
        repository=repo,
        embedding_provider=embedder,
        key_provider=key_prov,
    )


@pytest.mark.asyncio
async def test_memory_tools_lifecycle(memory_service: MemoryService) -> None:
    remember_tool = MemoryRememberTool(memory_service)
    list_tool = MemoryListTool(memory_service)
    search_tool = MemorySearchTool(memory_service)
    update_tool = MemoryUpdateTool(memory_service)
    forget_tool = MemoryForgetTool(memory_service)

    # 1. Remember
    res_rem = await remember_tool.execute({
        "content": "User prefers coffee over tea in the morning.",
        "kind": "preference",
        "subject": "user",
        "sensitivity": "normal",
    })
    assert res_rem.success is True
    mem_id = res_rem.data["id"]

    # 2. List
    res_list = await list_tool.execute({"kind": "preference"})
    assert res_list.success is True
    assert res_list.data["count"] == 1
    assert res_list.data["memories"][0]["id"] == mem_id

    # 3. Search
    res_search = await search_tool.execute({"query": "morning beverage"})
    assert res_search.success is True
    assert res_search.data["count"] == 1
    assert "coffee" in res_search.data["matches"][0]["content"]

    # 4. Update
    res_upd = await update_tool.execute({
        "memory_id": mem_id,
        "content": "User strictly drinks espresso in the morning.",
    })
    assert res_upd.success is True
    assert res_upd.data["revision"] == 2

    # 5. Forget
    res_forg = await forget_tool.execute({"memory_id": mem_id})
    assert res_forg.success is True
    assert res_forg.data["status"] == "deleted"

    # List after delete
    res_list2 = await list_tool.execute({})
    assert res_list2.data["count"] == 0


@pytest.mark.asyncio
async def test_memory_api_endpoints(memory_service: MemoryService) -> None:
    set_custom_memory_service(memory_service)
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Status
        st_res = await client.get("/v1/memory/status")
        assert st_res.status_code == 200
        assert st_res.json()["memory"]["enabled"] is True

        # Create
        create_payload = {
            "content": "Kullanıcı hafta sonları yürüyüş yapmayı sever.",
            "kind": "routine",
            "sensitivity": "normal",
            "subject": "user",
            "confidence": 0.95,
            "importance": 0.7,
        }
        c_res = await client.post("/v1/memories", json=create_payload)
        assert c_res.status_code == 201
        created_data = c_res.json()
        mem_id = created_data["id"]
        assert created_data["kind"] == "routine"

        # List
        l_res = await client.get("/v1/memories")
        assert l_res.status_code == 200
        items = l_res.json()
        assert len(items) == 1
        assert items[0]["id"] == mem_id

        # Search
        s_res = await client.get("/v1/memories/search?q=hafta sonu")
        assert s_res.status_code == 200
        search_data = s_res.json()
        assert search_data["count"] == 1

        # Patch
        p_res = await client.patch(f"/v1/memories/{mem_id}", json={"importance": 0.9})
        assert p_res.status_code == 200
        assert p_res.json()["importance"] == 0.9

        # Delete
        d_res = await client.delete(f"/v1/memories/{mem_id}")
        assert d_res.status_code == 200
        assert d_res.json()["status"] == "deleted"

    set_custom_memory_service(None)
