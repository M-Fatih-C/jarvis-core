"""Interactive and automated test script for Jarvis LLM adapter and agent runtime."""

import asyncio
from pathlib import Path
import sys

# Ensure repository root is in python search path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.runtime import AgentRuntime
from core.agent.state_machine import AgentState
from core.config.settings import get_settings
from core.llm.mlx_adapter import QwenMLXAdapter
from core.llm.mock_adapter import MockLLMAdapter
from core.logging.setup import setup_logging
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.mock import register_mock_tools
from core.tools.registry import ToolRegistry

console = Console()


def create_runtime(use_mock: bool = True) -> AgentRuntime:
    settings = get_settings()
    registry = ToolRegistry()
    register_mock_tools(registry)
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)

    if use_mock:
        llm = MockLLMAdapter()
    else:
        llm = QwenMLXAdapter(settings=settings)

    return AgentRuntime(
        llm_adapter=llm,
        tool_registry=registry,
        policy_engine=policy,
        tool_executor=executor,
        approval_store=ApprovalStore(default_ttl_seconds=settings.approval_ttl_seconds),
        context_builder=ContextBuilder(),
        settings=settings,
    )


async def run_test_suite(runtime: AgentRuntime) -> None:
    console.print(Panel.fit("[bold cyan]Jarvis V1 — Milestone 1 Acceptance & Scenario Suite[/bold cyan]"))

    table = Table(title="Test Verification Matrix")
    table.add_column("Test ID", style="bold yellow")
    table.add_column("Input Prompt", style="white")
    table.add_column("Expected Outcome", style="cyan")
    table.add_column("Actual State", style="magenta")
    table.add_column("Status", style="bold green")

    # ----------------------------------------------------
    # Test 1: Direct Conversational Response
    # ----------------------------------------------------
    console.print("\n[bold]Running Test 1:[/bold] 'Merhaba Jarvis.'")
    run1 = await runtime.run("Merhaba Jarvis.")
    success1 = (
        run1.state == AgentState.COMPLETED
        and run1.tool_call_count == 0
        and run1.final_response is not None
    )
    console.print(f"[dim]Jarvis:[/dim] {run1.final_response}")
    table.add_row(
        "Test 1",
        "Merhaba Jarvis.",
        "Normal text response, no tools",
        run1.state.value,
        "[green]PASS[/green]" if success1 else "[red]FAIL[/red]",
    )

    # ----------------------------------------------------
    # Test 2: System Status (R0 - Auto-execute)
    # ----------------------------------------------------
    console.print("\n[bold]Running Test 2:[/bold] 'Mac'in durumunu kontrol et.'")
    run2 = await runtime.run("Mac'in durumunu kontrol et.")
    success2 = (
        run2.state == AgentState.COMPLETED
        and run2.tool_call_count == 1
        and run2.pending_approval_id is None
    )
    console.print(f"[dim]Jarvis:[/dim] {run2.final_response}")
    table.add_row(
        "Test 2",
        "Mac'in durumunu kontrol et.",
        "system.get_status executed, no approval",
        run2.state.value,
        "[green]PASS[/green]" if success2 else "[red]FAIL[/red]",
    )

    # ----------------------------------------------------
    # Test 3: Reminder Creation (R2 - Approval Required)
    # ----------------------------------------------------
    console.print("\n[bold]Running Test 3 & Section 38 Acceptance Scenario:[/bold]")
    console.print("[dim]User:[/dim] Yarın 19:00'da YBS çalışmayı hatırlat.")
    run3 = await runtime.run("Yarın 19:00'da YBS çalışmayı hatırlat.")
    assert run3.state == AgentState.WAITING_APPROVAL
    console.print(f"[dim]Jarvis:[/dim] {run3.final_response}")
    console.print(f"[bold yellow]AgentState = {run3.state.value}[/bold yellow] (Approval ID: {run3.pending_approval_id})")

    # Follow-up: User approves
    console.print("[dim]User action:[/dim] [bold green]Approve[/bold green]")
    assert run3.pending_approval_id is not None
    completed_run3 = await runtime.resume_approval(run3.pending_approval_id)
    console.print(f"[dim]Jarvis:[/dim] {completed_run3.final_response}")
    console.print(f"[bold green]AgentState = {completed_run3.state.value}[/bold green]")

    success3 = (
        completed_run3.state == AgentState.COMPLETED
        and completed_run3.tool_call_count == 1
    )
    table.add_row(
        "Test 3",
        "Yarın 19:00'da YBS çalışmayı hatırlat.",
        "WAITING_APPROVAL -> Approve -> COMPLETED",
        f"{completed_run3.state.value} (post-approval)",
        "[green]PASS[/green]" if success3 else "[red]FAIL[/red]",
    )

    console.print("\n")
    console.print(table)


async def main() -> None:
    setup_logging()
    use_mock = True
    if len(sys.argv) > 1 and sys.argv[1] == "--mlx":
        use_mock = False

    mode_label = "Mock Adapter" if use_mock else "Local MLX Model"
    console.print(f"[bold green]Starting test runner using: {mode_label}[/bold green]")

    runtime = create_runtime(use_mock=use_mock)
    await run_test_suite(runtime)


if __name__ == "__main__":
    asyncio.run(main())
