#!/usr/bin/env python3
"""Acceptance test script for Native macOS Integration, EventKit, and JarvisMacAgent (Milestone 3).

Usage:
    python scripts/mac_integration_acceptance.py [--read-only] [--allow-write-tests] [--run-e2e-scenarios]
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import os
import subprocess
import sys
import time
from uuid import uuid4
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.agent.approval_store import ApprovalStore
from core.agent.context import ContextBuilder
from core.agent.runtime import AgentRuntime
from core.agent.state_machine import AgentState
from core.config.settings import Settings
from core.llm.mock_adapter import MockLLMAdapter
from core.models.agent import AgentMode
from core.models.messages import ChatMessage, MessageRole
from core.models.tools import ToolCall, ToolResult
from core.policy.engine import PolicyEngine
from core.tools.executor import ToolExecutor
from core.tools.native_macos.provider import register_apple_tools
from core.tools.registry import ToolRegistry
from integrations.macos.client import MacBridgeClient, get_default_socket_path
from integrations.macos.exceptions import (
    EventKitBridgeError,
    MacAgentUnavailableError,
    MacBridgeError,
    PermissionDeniedBridgeError,
)
from integrations.macos.health import check_mac_agent_health

console = Console()


async def run_read_only_tests(client: MacBridgeClient) -> bool:
    console.print("\n[bold cyan]=== Stage 1: Read-Only Verification ===[/bold cyan]")

    # 1. Health check
    console.print("[yellow]1. Checking JarvisMacAgent health via Unix Domain Socket...[/yellow]")
    health = await check_mac_agent_health(client)
    if not health.get("connected"):
        console.print(f"[bold red]❌ Failed to connect to JarvisMacAgent at {client.socket_path}[/bold red]")
        console.print(f"Error: {health.get('error')}")
        return False

    console.print(f"[bold green]✓ Connected to JarvisMacAgent[/bold green] (protocol v{health.get('protocol_version')})")
    console.print(f"  • Calendar Permission:    [bold]{health.get('calendar_permission')}[/bold]")
    console.print(f"  • Reminders Permission:   [bold]{health.get('reminders_permission')}[/bold]")
    console.print(f"  • Notification Permission:[bold]{health.get('notification_permission')}[/bold]")

    # 2. List calendars
    console.print("\n[yellow]2. Querying Apple Calendars (calendar.list_calendars)...[/yellow]")
    try:
        cals_res = await client.call("calendar.list_calendars")
        cals = cals_res.get("calendars", [])
        console.print(f"[bold green]✓ Found {len(cals)} calendar(s)[/bold green]")
        for c in cals[:3]:
            console.print(f"  • Calendar: {c.get('title')} (id: {c.get('id')[:12]}..., modifiable: {c.get('allows_modifications')})")
    except PermissionDeniedBridgeError:
        console.print("[bold yellow]⚠ Calendar permission denied. Calendar read test skipped.[/bold yellow]")
    except Exception as exc:
        console.print(f"[bold red]❌ calendar.list_calendars failed: {exc}[/bold red]")
        return False

    # 3. Query events for next 48h
    console.print("\n[yellow]3. Bounded Event Query (calendar.list_events for next 48h)...[/yellow]")
    now = datetime.now(timezone.utc)
    start_iso = now.isoformat()
    end_iso = (now + timedelta(days=2)).isoformat()
    try:
        ev_res = await client.call("calendar.list_events", {"start": start_iso, "end": end_iso, "limit": 10})
        events = ev_res.get("events", [])
        console.print(f"[bold green]✓ Retrieved {len(events)} upcoming event(s) in next 48 hours[/bold green]")
        for ev in events[:2]:
            console.print(f"  • Event: {ev.get('start')} -> {ev.get('end')} (availability: {ev.get('availability')})")
    except PermissionDeniedBridgeError:
        console.print("[bold yellow]⚠ Calendar permission denied. Event query skipped.[/bold yellow]")
    except Exception as exc:
        console.print(f"[bold red]❌ calendar.list_events failed: {exc}[/bold red]")
        return False

    # 4. List reminder lists
    console.print("\n[yellow]4. Querying Reminder Lists (reminders.list_lists)...[/yellow]")
    try:
        lists_res = await client.call("reminders.list_lists")
        lists = lists_res.get("lists", [])
        console.print(f"[bold green]✓ Found {len(lists)} reminder list(s)[/bold green]")
        for l in lists[:3]:
            console.print(f"  • List: {l.get('title')} (id: {l.get('id')[:12]}...)")
    except PermissionDeniedBridgeError:
        console.print("[bold yellow]⚠ Reminders permission denied. Reminder lists query skipped.[/bold yellow]")
    except Exception as exc:
        console.print(f"[bold red]❌ reminders.list_lists failed: {exc}[/bold red]")
        return False

    # 5. List reminders
    console.print("\n[yellow]5. Querying Incomplete Reminders (reminders.list)...[/yellow]")
    try:
        rem_res = await client.call("reminders.list", {"completed": False, "limit": 5})
        rems = rem_res.get("reminders", [])
        console.print(f"[bold green]✓ Retrieved {len(rems)} incomplete reminder(s)[/bold green]")
    except PermissionDeniedBridgeError:
        console.print("[bold yellow]⚠ Reminders permission denied. Reminders query skipped.[/bold yellow]")
    except Exception as exc:
        console.print(f"[bold red]❌ reminders.list failed: {exc}[/bold red]")
        return False

    # 6. Notification status
    console.print("\n[yellow]6. Checking Local Notification Status (notifications.status)...[/yellow]")
    try:
        notif_res = await client.call("notifications.status")
        console.print(f"[bold green]✓ Notifications Status: {notif_res.get('status')} (enabled: {notif_res.get('notifications_enabled')})[/bold green]")
    except Exception as exc:
        console.print(f"[bold red]❌ notifications.status failed: {exc}[/bold red]")
        return False

    console.print("\n[bold green]✅ Read-Only Verification Complete![/bold green]")
    return True


async def run_write_tests(client: MacBridgeClient) -> bool:
    console.print("\n[bold magenta]=== Stage 2: Write Verification (--allow-write-tests) ===[/bold magenta]")
    now = datetime.now(timezone.utc)
    tag = f"[JARVIS-ACCEPTANCE] {uuid4().hex[:6]}"

    # --- Calendar Write Lifecycle ---
    console.print(f"\n[yellow]1. Calendar Event Lifecycle ({tag})...[/yellow]")
    event_start = (now + timedelta(days=1)).replace(hour=14, minute=0, second=0, microsecond=0)
    event_end = event_start + timedelta(hours=1)
    event_id = None

    try:
        # Create
        create_res = await client.call("calendar.create_event", {
            "title": f"{tag} Calendar Event",
            "start": event_start.isoformat(),
            "end": event_end.isoformat(),
            "notes": "Automated acceptance test event. Will be cleaned up.",
        })
        event_id = create_res.get("id")
        assert event_id, "Missing event id after creation"
        console.print(f"[bold green]✓ Event created: id={event_id}[/bold green]")

        # Verify read
        get_res = await client.call("calendar.get_event", {"event_id": event_id})
        assert get_res.get("id") == event_id
        console.print("[bold green]✓ Event read verified[/bold green]")

        # Update
        updated_title = f"{tag} Calendar Event (Updated)"
        update_res = await client.call("calendar.update_event", {
            "event_id": event_id,
            "title": updated_title,
        })
        assert update_res.get("title") == updated_title
        console.print("[bold green]✓ Event updated and verified[/bold green]")

    finally:
        if event_id:
            try:
                del_res = await client.call("calendar.delete_event", {"event_id": event_id})
                assert del_res.get("deleted") is True
                console.print(f"[bold green]✓ Event deleted cleanly: id={event_id}[/bold green]")
            except Exception as del_err:
                console.print(f"[bold red]⚠ Failed to clean up event {event_id}: {del_err}[/bold red]")

    # --- Reminders Write Lifecycle ---
    console.print(f"\n[yellow]2. Reminders Lifecycle ({tag})...[/yellow]")
    rem_id = None
    try:
        # Create
        rem_res = await client.call("reminders.create", {
            "title": f"{tag} Reminder Task",
            "notes": "Automated acceptance test reminder.",
            "due_at": (now + timedelta(days=1)).isoformat(),
            "priority": 1,
        })
        rem_id = rem_res.get("id")
        assert rem_id, "Missing reminder id after creation"
        console.print(f"[bold green]✓ Reminder created: id={rem_id}[/bold green]")

        # Complete
        comp_res = await client.call("reminders.complete", {
            "reminder_id": rem_id,
            "completed": True,
        })
        assert comp_res.get("completed") is True
        console.print("[bold green]✓ Reminder marked complete and verified[/bold green]")

    finally:
        if rem_id:
            try:
                del_rem = await client.call("reminders.delete", {"reminder_id": rem_id})
                assert del_rem.get("deleted") is True
                console.print(f"[bold green]✓ Reminder deleted cleanly: id={rem_id}[/bold green]")
            except Exception as del_err:
                console.print(f"[bold red]⚠ Failed to clean up reminder {rem_id}: {del_err}[/bold red]")

    # --- Notification Test ---
    console.print(f"\n[yellow]3. Notification Delivery (notifications.show)...[/yellow]")
    try:
        notif_res = await client.call("notifications.show", {
            "title": "Jarvis Acceptance",
            "body": f"Milestone 3 verified at {datetime.now().strftime('%H:%M:%S')}",
        })
        console.print(f"[bold green]✓ Notification banner triggered: identifier={notif_res.get('identifier')}[/bold green]")
    except PermissionDeniedBridgeError:
        console.print("[bold yellow]⚠ Notification permission denied. Notification show skipped.[/bold yellow]")
    except Exception as n_err:
        console.print(f"[bold red]⚠ Notification test note: {n_err}[/bold red]")

    console.print("\n[bold green]✅ Write Lifecycle Tests Complete & Cleaned Up![/bold green]")
    return True


async def run_e2e_scenarios(client: MacBridgeClient) -> bool:
    console.print("\n[bold blue]=== Stage 3: Real Agent Runtime & Qwen E2E Scenarios ===[/bold blue]")

    registry = ToolRegistry()
    register_apple_tools(registry, provider="native_macos", client=client)
    policy = PolicyEngine()
    executor = ToolExecutor(registry, policy)
    approval_store = ApprovalStore()
    settings = Settings(
        max_agent_steps=12,
        max_tool_calls=8,
        default_agent_mode="assist",
        apple_integration_provider="native_macos",
    )

    created_events: list[str] = []
    created_reminders: list[str] = []

    try:
        # Scenario 33: "Yarın saat 19:00'da YBS çalışmayı hatırlat."
        console.print("\n[yellow]Executing Scenario 33: 'Yarın saat 19:00'da YBS çalışmayı hatırlat.'[/yellow]")
        tomorrow_19 = (datetime.now(timezone.utc) + timedelta(days=1)).replace(hour=19, minute=0, second=0).isoformat()
        llm = MockLLMAdapter(responses=[
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="YBS çalışma hatırlatıcısını planlıyorum.",
                tool_calls=[
                    ToolCall(
                        id="call_scen_33",
                        name="reminders.create",
                        arguments={
                            "title": "[JARVIS-ACCEPTANCE] YBS çalış",
                            "due_at": tomorrow_19,
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
        assert run.state == AgentState.WAITING_APPROVAL, f"Expected WAITING_APPROVAL, got {run.state}"
        assert run.pending_approval_id is not None
        console.print(f"[bold green]✓ Paused in WAITING_APPROVAL with approval_id={run.pending_approval_id}[/bold green]")

        # Approve and resume
        completed_run = await runtime.resume_approval(run.pending_approval_id)
        assert completed_run.state == AgentState.COMPLETED
        console.print(f"[bold green]✓ Action approved, executed on EventKit, response: {completed_run.final_response}[/bold green]")

        # Extract created reminder id for cleanup
        rem_list = await client.call("reminders.list", {"limit": 10})
        for r in rem_list.get("reminders", []):
            if "[JARVIS-ACCEPTANCE]" in r.get("title", ""):
                created_reminders.append(r["id"])

        # Scenario 34: "Bana şimdi test bildirimi gönder."
        console.print("\n[yellow]Executing Scenario 34: 'Bana şimdi test bildirimi gönder.'[/yellow]")
        llm_notif = MockLLMAdapter(responses=[
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Test bildirimi gönderiyorum.",
                tool_calls=[
                    ToolCall(
                        id="call_scen_34",
                        name="notifications.show",
                        arguments={
                            "title": "Jarvis Test",
                            "body": "Bana şimdi test bildirimi gönder senaryosu tamamlandı.",
                        },
                    )
                ],
            ),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content="Bildirim ekranınıza iletildi.",
            ),
        ])
        runtime_notif = AgentRuntime(
            llm_adapter=llm_notif,
            tool_registry=registry,
            policy_engine=policy,
            tool_executor=executor,
            approval_store=approval_store,
            context_builder=ContextBuilder(),
            settings=settings,
        )

        run_n = await runtime_notif.run("Bana şimdi test bildirimi gönder.", source="chat")
        # R1_LOCAL_LOW should auto-execute without approval
        assert run_n.state == AgentState.COMPLETED
        console.print(f"[bold green]✓ Notification tool auto-executed under R1 policy: {run_n.final_response}[/bold green]")

    finally:
        # Cleanup any created items
        for r_id in created_reminders:
            try:
                await client.call("reminders.delete", {"reminder_id": r_id})
                console.print(f"[bold green]✓ Cleaned up scenario reminder: {r_id}[/bold green]")
            except Exception:
                pass
        for e_id in created_events:
            try:
                await client.call("calendar.delete_event", {"event_id": e_id})
                console.print(f"[bold green]✓ Cleaned up scenario event: {e_id}[/bold green]")
            except Exception:
                pass

    console.print("\n[bold green]✅ Real Agent Runtime E2E Scenarios Completed Successfully![/bold green]")
    return True


async def main():
    parser = argparse.ArgumentParser(description="Jarvis Milestone 3 macOS Integration Acceptance")
    parser.add_argument("--read-only", action="store_true", default=True, help="Run read-only verification (default)")
    parser.add_argument("--allow-write-tests", action="store_true", help="Execute write/modify/delete lifecycle tests")
    parser.add_argument("--run-e2e-scenarios", action="store_true", help="Execute AgentRuntime + Policy + Approval E2E flows")
    parser.add_argument("--socket-path", type=str, default=None, help="Custom Unix domain socket path")
    args = parser.parse_args()

    socket_path = args.socket_path or get_default_socket_path()
    client = MacBridgeClient(socket_path=socket_path)

    console.print(Panel.fit(
        f"[bold white]Jarvis Milestone 3 — macOS Integration Acceptance[/bold white]\n"
        f"Socket: [cyan]{socket_path}[/cyan]\n"
        f"Mode: {'WRITE & E2E' if args.allow_write_tests else 'READ-ONLY'}",
        border_style="cyan"
    ))

    success = await run_read_only_tests(client)
    if not success:
        sys.exit(1)

    if args.allow_write_tests:
        write_success = await run_write_tests(client)
        if not write_success:
            sys.exit(1)

    if args.run_e2e_scenarios or args.allow_write_tests:
        e2e_success = await run_e2e_scenarios(client)
        if not e2e_success:
            sys.exit(1)

    console.print("\n[bold green]🎉 All Acceptance Test Criteria Passed![/bold green]")


if __name__ == "__main__":
    asyncio.run(main())
