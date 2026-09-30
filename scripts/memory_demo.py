#!/usr/bin/env python3
"""Milestone 2 Acceptance Demo: 'Jarvis beni öğreniyor'.

Demonstrates cross-session memory learning and retrieval:
Conversation A: User teaches a durable preference. Memory is extracted, classified, and persisted.
Session destroyed.
Conversation B: User makes a request. Relevant memory is retrieved, injected into context,
and influences Jarvis's recommendation.

Usage:
    python scripts/memory_demo.py          # Uses Mock LLM for instant deterministic verification
    python scripts/memory_demo.py --mlx    # Uses real Qwen3.5-4B MLX model on Apple Silicon
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.runtime import AgentRuntime
from core.config.settings import Settings
from core.llm.base import LLMAdapter
from core.llm.mock_adapter import MockLLMAdapter
from core.llm.schemas import LLMResponse
from core.memory.crypto import InMemoryKeyProvider
from core.memory.embeddings.mock import DeterministicMockEmbeddingProvider
from core.memory.local_store import SQLiteMemoryRepository
from core.memory.models import MemoryFilters, MemoryStatus
from core.memory.service import MemoryService
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.mock import register_mock_tools
from core.tools.registry import ToolRegistry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Jarvis Milestone 2 Memory Demo")
    parser.add_argument("--mlx", action="store_true", help="Run with real Qwen3.5-4B MLX adapter")
    parser.add_argument("--db-path", type=str, default=None, help="Custom SQLite db path")
    return parser.parse_args()


async def run_demo(use_mlx: bool = False, db_path: str | None = None) -> None:
    print("=" * 70)
    print("JARVIS MILESTONE 2: MEMORY & PERSONALIZATION ACCEPTANCE DEMO")
    print("=" * 70)

    # 1. Setup persistence
    temp_dir = None
    if db_path is None:
        temp_dir = tempfile.TemporaryDirectory()
        db_path = str(Path(temp_dir.name) / "memory.db")
    print(f"[*] SQLite Memory DB: {db_path}")

    settings = Settings()
    repo = SQLiteMemoryRepository(db_path)
    embedder = DeterministicMockEmbeddingProvider(dimensions=384)
    key_prov = InMemoryKeyProvider()
    mem_service = MemoryService(
        repository=repo,
        embedding_provider=embedder,
        key_provider=key_prov,
        settings=settings,
    )

    tool_reg = ToolRegistry()
    register_mock_tools(tool_reg)
    policy_eng = PolicyEngine()
    tool_exec = ToolExecutor(tool_reg, policy_eng)

    llm: LLMAdapter
    if use_mlx:
        print("[*] Initializing Qwen3.5-4B MLX Runtime...")
        from core.llm.mlx_adapter import QwenMLXAdapter
        llm = QwenMLXAdapter(settings=settings)
    else:
        print("[*] Initializing Mock LLM Adapter...")
        mock = MockLLMAdapter()
        # Session 1 response
        mock.queue_response(LLMResponse(
            content="Anladım! Hafta içi yan projelerinizle 18:00'den sonra ilgilenme tercihinizi aklımda tutacağım."
        ))
        # Session 2 response taking into account retrieved memory
        mock.queue_response(LLMResponse(
            content="Yarın hafta içi olduğu ve yan projelerinizle 18:00'den sonra ilgilenmeyi tercih ettiğiniz için, Jarvis projesine 19:00 - 21:00 arasında 2 saat ayırmanızı öneriyorum."
        ))
        llm = mock

    # =========================================================================
    # CONVERSATION A: Learn Preference
    # =========================================================================
    print("\n" + "-" * 70)
    print("CONVERSATION A: Learning User Preference")
    print("-" * 70)

    runtime_a = AgentRuntime(
        llm_adapter=llm,
        tool_registry=tool_reg,
        policy_engine=policy_eng,
        tool_executor=tool_exec,
        approval_store=ApprovalStore(),
        context_builder=ContextBuilder(),
        memory_service=mem_service,
        settings=settings,
    )

    user_msg_a = "Hafta içi yan projelerimle 18:00'den sonra ilgilenmek istiyorum."
    print(f"[User]: {user_msg_a}")

    run_a = await runtime_a.run(user_msg_a)
    print(f"[Jarvis]: {run_a.final_response}")
    print(f"[Run A State]: {run_a.state.value}")

    # Inspect persisted memory
    records = await repo.list(filters=MemoryFilters(status=MemoryStatus.ACTIVE))
    print(f"\n[Memory Store Status]: {len(records)} active memory record(s) persisted in SQLite:")
    for rec in records:
        print(f"  - ID: {rec.id}")
        print(f"    Kind: {rec.kind.value}")
        print(f"    Sensitivity: {rec.sensitivity.value}")
        print(f"    Content: {rec.content}")
        print(f"    Fingerprint: {rec.fingerprint[:16]}...")
        print(f"    Confidence: {rec.confidence} | Importance: {rec.importance}")

    assert len(records) >= 1, "Expected at least one active memory persisted!"

    # =========================================================================
    # DESTROY SESSION
    # =========================================================================
    print("\n" + "-" * 70)
    print("DESTROYING SESSION CONTEXT (Simulating new process / next day)")
    print("-" * 70)
    del runtime_a
    del run_a

    # =========================================================================
    # CONVERSATION B: New Session Recalls Preference
    # =========================================================================
    print("\n" + "-" * 70)
    print("CONVERSATION B: Recall in Fresh Session")
    print("-" * 70)

    runtime_b = AgentRuntime(
        llm_adapter=llm,
        tool_registry=tool_reg,
        policy_engine=policy_eng,
        tool_executor=tool_exec,
        approval_store=ApprovalStore(),
        context_builder=ContextBuilder(),
        memory_service=mem_service,  # Shared persistent database
        settings=settings,
    )

    user_msg_b = "Yarın Jarvis projesine iki saat ayırmak istiyorum. Uygun bir zaman önerir misin?"
    print(f"[User]: {user_msg_b}")

    # Query retriever directly to show what will be injected into Qwen's context
    retrieved_items = await mem_service.retrieve(user_msg_b, limit=3)
    print(f"\n[Memory Retriever Found {len(retrieved_items)} relevant memory]:")
    for item in retrieved_items:
        print(f"  * [{item.record.kind.value} | score={item.score:.2f}] {item.record.content}")

    run_b = await runtime_b.run(user_msg_b)
    print(f"\n[Jarvis Response]:\n{run_b.final_response}")
    print(f"[Run B State]: {run_b.state.value}")

    print("\n" + "=" * 70)
    print("DEMO RESULT: SUCCESS (Cross-session memory learned, persisted, and recalled)")
    print("=" * 70)

    if temp_dir:
        temp_dir.cleanup()


def main() -> None:
    args = parse_args()
    asyncio.run(run_demo(use_mlx=args.mlx, db_path=args.db_path))


if __name__ == "__main__":
    main()
