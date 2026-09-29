"""Interactive and automated test script for Jarvis LLM adapter and agent runtime."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

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
        context_builder=ContextBuilder(timezone_name=settings.default_timezone),
        settings=settings,
    )


async def run_test_suite(runtime: AgentRuntime) -> None:
    settings = get_settings()
    console.print(Panel.fit("[bold cyan]Jarvis V1 — Milestone 1.1 Acceptance & Scenario Suite[/bold cyan]"))

    table = Table(title="Test Verification Matrix")
    table.add_column("Scenario", style="bold yellow")
    table.add_column("Input Prompt", style="white")
    table.add_column("Expected Outcome", style="cyan")
    table.add_column("Actual State", style="magenta")
    table.add_column("Status", style="bold green")

    # ----------------------------------------------------
    # Baseline Test: Conversational Message
    # ----------------------------------------------------
    console.print("\n[bold]Running Baseline Chat Test:[/bold] 'Merhaba Jarvis.'")
    run0 = await runtime.run("Merhaba Jarvis.")
    success0 = (
        run0.state == AgentState.COMPLETED
        and run0.tool_call_count == 0
        and run0.final_response is not None
    )
    console.print(f"[dim]Jarvis:[/dim] {run0.final_response}")
    table.add_row(
        "Baseline",
        "Merhaba Jarvis.",
        "Normal text response, no tools",
        run0.state.value,
        "[green]PASS[/green]" if success0 else "[red]FAIL[/red]",
    )

    # ----------------------------------------------------
    # Scenario A: Read Tool (R0 ALLOW, No Approval)
    # ----------------------------------------------------
    console.print("\n[bold]Running Scenario A (Read Tool):[/bold] 'Mac'in durumunu kontrol et.'")
    run_a = await runtime.run("Mac'in durumunu kontrol et.")
    success_a = (
        run_a.state == AgentState.COMPLETED
        and run_a.tool_call_count == 1
        and run_a.pending_approval_id is None
        and "online" in (run_a.final_response or "").lower()
    )
    console.print(f"[dim]Jarvis:[/dim] {run_a.final_response}")
    table.add_row(
        "Scenario A",
        "Mac'in durumunu kontrol et.",
        "system.get_status executed (R0), no approval",
        run_a.state.value,
        "[green]PASS[/green]" if success_a else "[red]FAIL[/red]",
    )

    # ----------------------------------------------------
    # Scenario B: Relative Date + Approval (R2 WRITE)
    # ----------------------------------------------------
    now_tz = datetime.now(ZoneInfo(settings.default_timezone))
    expected_tomorrow_str = (now_tz + timedelta(days=1)).strftime("%Y-%m-%d")

    console.print(f"\n[bold]Running Scenario B (Relative Date + Approval):[/bold] Current date: {now_tz.strftime('%Y-%m-%d')}")
    console.print("[dim]User:[/dim] Yarın saat 19:00'da YBS çalışmayı hatırlat.")

    run_b = await runtime.run("Yarın saat 19:00'da YBS çalışmayı hatırlat.")
    assert run_b.state == AgentState.WAITING_APPROVAL, f"Expected WAITING_APPROVAL, got {run_b.state}"
    assert run_b.pending_tool_call is not None
    assert run_b.pending_tool_call.name == "reminders.create"

    due_at = str(run_b.pending_tool_call.arguments.get("due_at", ""))
    console.print(f"[dim]Proposed due_at:[/dim] {due_at}")
    valid_date = expected_tomorrow_str in due_at
    console.print(f"[dim]Jarvis response:[/dim] {run_b.final_response}")
    console.print(f"[bold yellow]AgentState = {run_b.state.value}[/bold yellow] (Approval ID: {run_b.pending_approval_id})")

    # User approves
    console.print("[dim]User action:[/dim] [bold green]Approve[/bold green]")
    assert run_b.pending_approval_id is not None
    completed_run_b = await runtime.resume_approval(run_b.pending_approval_id)
    console.print(f"[dim]Jarvis:[/dim] {completed_run_b.final_response}")
    console.print(f"[bold green]AgentState = {completed_run_b.state.value}[/bold green]")

    success_b = (
        completed_run_b.state == AgentState.COMPLETED
        and completed_run_b.tool_call_count == 1
        and valid_date
    )
    table.add_row(
        "Scenario B",
        "Yarın saat 19:00'da YBS çalışmayı hatırlat.",
        f"reminders.create ({expected_tomorrow_str}) -> WAITING_APPROVAL -> Approve -> COMPLETED",
        f"{completed_run_b.state.value} (post-approval)",
        "[green]PASS[/green]" if success_b else "[red]FAIL[/red]",
    )

    # ----------------------------------------------------
    # Scenario C: Multi-Step Tool Flow (calendar -> reminder)
    # ----------------------------------------------------
    console.print("\n[bold]Running Scenario C (Multi-step tool reasoning):[/bold]")
    console.print("[dim]User:[/dim] Takvimime bak ve uygun bir zamana YBS çalışma hatırlatıcısı ekle.")

    run_c = await runtime.run("Takvimime bak ve uygun bir zamana YBS çalışma hatırlatıcısı ekle.")
    assert run_c.state == AgentState.WAITING_APPROVAL, f"Expected WAITING_APPROVAL on second tool, got {run_c.state}"
    assert run_c.tool_call_count == 1, "Expected calendar.list_events to have executed first"
    assert run_c.pending_tool_call is not None
    assert run_c.pending_tool_call.name == "reminders.create"
    console.print(f"[dim]First step executed:[/dim] calendar.list_events (tool_count: 1)")
    console.print(f"[dim]Second step proposed:[/dim] reminders.create (waiting approval)")
    console.print(f"[dim]Jarvis response:[/dim] {run_c.final_response}")

    # User approves second tool
    console.print("[dim]User action:[/dim] [bold green]Approve[/bold green]")
    assert run_c.pending_approval_id is not None
    completed_run_c = await runtime.resume_approval(run_c.pending_approval_id)
    console.print(f"[dim]Jarvis:[/dim] {completed_run_c.final_response}")
    console.print(f"[bold green]AgentState = {completed_run_c.state.value}[/bold green]")

    success_c = (
        completed_run_c.state == AgentState.COMPLETED
        and completed_run_c.tool_call_count == 2
    )
    table.add_row(
        "Scenario C",
        "Takvimime bak ve uygun bir zamana YBS çalışma hatırlatıcısı ekle.",
        "calendar.list_events (R0) -> reminders.create (R2) -> Approve -> COMPLETED",
        f"{completed_run_c.state.value} (tools: {completed_run_c.tool_call_count})",
        "[green]PASS[/green]" if success_c else "[red]FAIL[/red]",
    )

    console.print("\n")
    console.print(table)


async def main() -> None:
    setup_logging()
    use_mock = True
    if len(sys.argv) > 1 and sys.argv[1] == "--mlx":
        use_mock = False

    mode_label = "Mock Adapter" if use_mock else "Local MLX Model (mlx-community/Qwen3.5-4B-MLX-4bit)"
    console.print(f"[bold green]Starting test runner using: {mode_label}[/bold green]")

    runtime = create_runtime(use_mock=use_mock)
    await run_test_suite(runtime)


if __name__ == "__main__":
    asyncio.run(main())
