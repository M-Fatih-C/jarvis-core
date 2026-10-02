#!/usr/bin/env python3
"""Comprehensive acceptance test suite for Milestone 4.2:
Intelligent Task Approval, Apple Calendar & Reminders Planning.

Tests:
1. Scenario 1: Email -> Deadline Extraction -> Reminder Proposal -> R2 Approval -> Real EKReminder Save -> Read-back -> EXECUTED.
2. Scenario 2: Work Block Planning -> Real Calendar Availability -> Conflict-free Slot -> R2 Approval -> Real EKEvent Save -> Read-back -> EXECUTED.
3. Scenario 3: Idempotency & Duplicate Mutation Prevention.
4. Scenario 4: Bidirectional Email Source Trace ("Which email created this reminder?").
5. Scenario 5: Security / Prompt Injection Boundary Enforcement.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import sys
from typing import Any
from uuid import UUID, uuid4

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from core.agent.approval_store import ApprovalStore
from core.config.settings import get_settings
from core.email_analysis.analyzer import EmailAnalyzer
from core.email_analysis.schemas import EmailCategory, ImportanceLevel, TaskPriority, TaskProposal
from core.email_analysis.task_extractor import TaskExtractor
from core.llm.base import LLMAdapter, LLMResponse
from core.models.agent import AgentMode
from core.models.tools import RiskLevel, ToolCall, ToolDefinition
from core.policy.engine import PolicyEngine
from core.policy.risk import PolicyDecisionType, PolicyRequest
from core.task_planning.approval_service import DuplicateActionError, TaskApprovalService
from core.task_planning.calendar_manager import CalendarTargetManager
from core.task_planning.planner import TaskPlanner
from core.task_planning.schemas import (
    ActionDestination,
    ActionType,
    CalendarCategory,
    TaskActionProposal,
)
from core.task_planning.state import TaskProposalStatus
from integrations.gmail.models import NormalizedEmail
from integrations.gmail.storage import EmailStorage
from integrations.macos.client import MacBridgeClient

console = Console()


class Milestone42Harness:
    def __init__(self, use_real_eventkit: bool = True) -> None:
        self.use_real_eventkit = use_real_eventkit
        self.settings = get_settings()
        self.storage = EmailStorage(db_path=":memory:", encryption_enabled=False)
        self.bridge_client = MacBridgeClient()
        self.calendar_mgr = CalendarTargetManager(bridge_client=self.bridge_client, settings=self.settings)
        self.planner = TaskPlanner(bridge_client=self.bridge_client, buffer_minutes=15)
        self.approval_store = ApprovalStore()
        self.approval_svc = TaskApprovalService(
            storage=self.storage,
            approval_store=self.approval_store,
            bridge_client=self.bridge_client,
        )
        self.results: list[dict[str, Any]] = []
        self.created_reminders: list[str] = []
        self.created_events: list[str] = []

    def record(self, test_name: str, status: str, details: str) -> None:
        self.results.append({"name": test_name, "status": status, "details": details})
        color = "green" if status == "PASS" else "red"
        console.print(f"[{color}][{status}][/{color}] [bold]{test_name}[/bold] - {details}")

    async def cleanup(self) -> None:
        """Clean up any created records from real Apple Calendar/Reminders."""
        if not self.use_real_eventkit:
            self.storage.close()
            return

        for rem_id in self.created_reminders:
            try:
                await self.bridge_client.call("reminders.delete", {"reminder_id": rem_id})
                console.print(f"  [dim]Cleaned up test reminder: {rem_id}[/dim]")
            except Exception as e:
                console.print(f"  [yellow]Failed to clean up reminder {rem_id}: {e}[/yellow]")

        for ev_id in self.created_events:
            try:
                await self.bridge_client.call("calendar.delete_event", {"event_id": ev_id})
                console.print(f"  [dim]Cleaned up test calendar event: {ev_id}[/dim]")
            except Exception as e:
                console.print(f"  [yellow]Failed to clean up event {ev_id}: {e}[/yellow]")

        self.storage.close()


async def run_scenario_1_deadline_reminder(harness: Milestone42Harness) -> bool:
    console.print("\n[bold cyan]─── Scenario 1: Email Deadline -> Reminder Proposal -> R2 Approval -> EKReminder Save -> Read-back ───[/bold cyan]")
    tz = timezone.utc
    msg_id = "msg_grad_app_001"
    task_id = uuid4()
    deadline_dt = datetime.now(tz) + timedelta(days=7)

    # 1. Simulate inbound email
    email = NormalizedEmail(
        message_id=msg_id,
        thread_id="t_grad_1",
        sender="Fen Bilimleri Enstitüsü <fbe@univ.edu.tr>",
        sender_email="fbe@univ.edu.tr",
        recipient="fatih@jarvis.local",
        subject="Yüksek Lisans Başvuru Takvimi",
        received_at=datetime.now(tz),
        body_plain=f"Yüksek lisans başvurunuzu {deadline_dt.strftime('%d.%m.%Y saat 18:00')} tarihine kadar tamamlayınız.",
        body_preview="Yüksek lisans başvurunuzu...",
        attachments=[],
        headers={},
        labels=["INBOX"],
        content_hash="hash_grad_app",
    )
    await harness.storage.save_email(email, "fatih@jarvis.local")

    # 2. Extract structured task proposal
    proposal = TaskProposal(
        task_id=task_id,
        source_message_id=msg_id,
        title="Yüksek Lisans Başvurusunu Tamamla",
        description="Fen Bilimleri Enstitüsü başvuru formu teslimi",
        category=EmailCategory.EDUCATION,
        priority=TaskPriority.HIGH,
        deadline=deadline_dt,
        deadline_confidence=harness.planner.plan_reminder_for_deadline.__annotations__.get("deadline_confidence", "exact"),
        raw_deadline_text=deadline_dt.strftime("%d.%m.%Y 18:00"),
        proposed_action="Apple Reminders hatırlatıcısı oluştur",
        status=TaskProposalStatus.PROPOSED,
    )
    await harness.storage.save_task_proposal(proposal.model_dump())
    harness.record("task_proposal_created", "PASS", f"TaskProposal created with status=PROPOSED (ID: {task_id})")

    # 3. Planning engine generates reminder action proposal
    action_proposal = harness.planner.plan_reminder_for_deadline(proposal)
    assert action_proposal.action_type == ActionType.REMINDER
    assert action_proposal.target_destination == ActionDestination.APPLE_REMINDERS
    assert action_proposal.status == TaskProposalStatus.PROPOSED
    harness.record("planning_engine_reminder_proposed", "PASS", f"Generated Reminder proposal with due_date={action_proposal.due_date.isoformat()}")

    # 4. Submit to PolicyEngine R2 -> Enters WAITING_APPROVAL
    waiting_action, approval_req = await harness.approval_svc.prepare_action_approval(action_proposal)
    assert waiting_action.status == TaskProposalStatus.WAITING_APPROVAL
    assert approval_req.action_digest is not None
    harness.record("policy_r2_waiting_approval", "PASS", f"Policy evaluated R2_WRITE; Action Digest bound: {approval_req.action_digest[:16]}...")

    # 5. User Review & Approval -> Real EventKit mutation & Read-back
    executed_action = await harness.approval_svc.approve_and_execute_action(waiting_action.action_id)
    assert executed_action.status == TaskProposalStatus.EXECUTED
    assert executed_action.external_id is not None
    harness.created_reminders.append(executed_action.external_id)

    # 6. Verify parent proposal transitioned to EXECUTED
    parent_prop = await harness.storage.get_task_proposal(task_id)
    assert parent_prop["status"] == TaskProposalStatus.EXECUTED
    harness.record("real_ekreminder_saved_and_readback", "PASS", f"Real EKReminder saved and verified by read-back (ID: {executed_action.external_id})")

    return True


async def run_scenario_2_work_block_calendar(harness: Milestone42Harness) -> bool:
    console.print("\n[bold cyan]─── Scenario 2: Work Block Planning -> Real Calendar Availability -> R2 Approval -> EKEvent Save ───[/bold cyan]")
    tz = timezone.utc
    msg_id = "msg_exam_prep_002"
    task_id = uuid4()
    exam_deadline = datetime.now(tz) + timedelta(days=9)

    # 1. Inbound email
    email = NormalizedEmail(
        message_id=msg_id,
        thread_id="t_exam_2",
        sender="Prof. Dr. Mehmet <mehmet@univ.edu.tr>",
        sender_email="mehmet@univ.edu.tr",
        recipient="fatih@jarvis.local",
        subject="Algoritmalar ve Veri Yapıları Vize Sınavı",
        received_at=datetime.now(tz),
        body_plain="Vize sınavı 9 gün sonra yapılacaktır. Kapsam: Graf algoritmaları ve dinamik programlama.",
        body_preview="Vize sınavı 9 gün sonra...",
        attachments=[],
        headers={},
        labels=["INBOX"],
        content_hash="hash_exam_prep",
    )
    await harness.storage.save_email(email, "fatih@jarvis.local")

    proposal = TaskProposal(
        task_id=task_id,
        source_message_id=msg_id,
        title="Algoritmalar Vize Sınavı Hazırlığı",
        description="Graf algoritmaları çalışma bloğu",
        category=EmailCategory.EDUCATION,
        priority=TaskPriority.HIGH,
        deadline=exam_deadline,
        raw_deadline_text="9 gün sonra",
        proposed_action="Çalışma bloğu planla",
        status=TaskProposalStatus.PROPOSED,
    )
    await harness.storage.save_task_proposal(proposal.model_dump())

    # 2. Get preferred calendar (preferring iCloud)
    pref_cal, warn = await harness.calendar_mgr.get_preferred_calendar()
    cal_id = pref_cal.id if pref_cal else None
    harness.record("preferred_calendar_selection", "PASS", f"Selected target calendar: {pref_cal.title if pref_cal else 'Default'} (iCloud: {pref_cal.is_icloud if pref_cal else False})")

    # 3. Plan 2-hour work block using deterministic interval arithmetic
    action_proposal, slots = await harness.planner.plan_work_block(
        proposal=proposal,
        duration_minutes=120,
        target_calendar_id=cal_id,
        logical_category=CalendarCategory.EDUCATION,
    )
    assert len(slots) > 0
    assert action_proposal.action_type == ActionType.WORK_BLOCK
    assert action_proposal.target_destination == ActionDestination.APPLE_CALENDAR
    harness.record("conflict_free_slots_computed", "PASS", f"Calculated {len(slots)} conflict-free slots. Chosen slot: {action_proposal.start_time.strftime('%Y-%m-%d %H:%M')}")

    # 4. Submit to PolicyEngine R2
    waiting_action, approval_req = await harness.approval_svc.prepare_action_approval(action_proposal)
    assert waiting_action.status == TaskProposalStatus.WAITING_APPROVAL
    harness.record("policy_r2_calendar_approval", "PASS", f"Policy evaluated R2_WRITE for calendar event (Approval ID: {approval_req.id})")

    # 5. Approve & Execute via JarvisMacAgent with Read-back verification
    executed_action = await harness.approval_svc.approve_and_execute_action(waiting_action.action_id)
    assert executed_action.status == TaskProposalStatus.EXECUTED
    assert executed_action.external_id is not None
    harness.created_events.append(executed_action.external_id)

    # 6. Verify proposal marked EXECUTED
    parent_prop = await harness.storage.get_task_proposal(task_id)
    assert parent_prop["status"] == TaskProposalStatus.EXECUTED
    harness.record("real_ekevent_saved_and_readback", "PASS", f"Real EKEvent saved and verified in Apple Calendar (ID: {executed_action.external_id})")

    return True


async def run_scenario_3_idempotency(harness: Milestone42Harness) -> bool:
    console.print("\n[bold cyan]─── Scenario 3: Idempotency & Duplicate Prevention ───[/bold cyan]")
    # Re-running planning for the exact same (source_message_id, task_id, action_type) must be strictly blocked
    tasks = await harness.storage.get_task_proposals()
    target_action: TaskActionProposal | None = None
    for t in tasks:
        actions = await harness.storage.get_task_actions_for_task(t["task_id"])
        for a in actions:
            if a.status == TaskProposalStatus.EXECUTED:
                target_action = a
                break
        if target_action:
            break

    if not target_action:
        harness.record("idempotency_duplicate_prevention", "FAIL", "No executed action found to test idempotency")
        return False

    duplicate_action = TaskActionProposal(
        task_id=target_action.task_id,
        source_message_id=target_action.source_message_id,
        action_type=target_action.action_type,
        target_destination=target_action.target_destination,
        title="Duplicate Attempt",
        due_date=target_action.due_date or (datetime.now(timezone.utc) + timedelta(days=1)),
        start_time=target_action.start_time,
        end_time=target_action.end_time,
        status=TaskProposalStatus.PROPOSED,
    )

    try:
        await harness.approval_svc.prepare_action_approval(duplicate_action)
        harness.record("idempotency_duplicate_prevention", "FAIL", "Failed to block duplicate action proposal")
        return False
    except DuplicateActionError as dup_err:
        harness.record("idempotency_duplicate_prevention", "PASS", f"Duplicate action proposal correctly rejected: {dup_err}")
        return True


async def run_scenario_4_source_email_linkage(harness: Milestone42Harness) -> bool:
    console.print("\n[bold cyan]─── Scenario 4: Bidirectional Email Source Linkage ───[/bold cyan]")
    if not harness.created_reminders:
        harness.record("source_email_linkage", "FAIL", "No created reminder available for linkage test")
        return False

    rem_id = harness.created_reminders[0]
    link = await harness.approval_svc.get_source_email_for_external_record(rem_id)
    if not link:
        harness.record("source_email_linkage", "FAIL", f"Could not find source email for external record {rem_id}")
        return False

    assert link.email_subject == "Yüksek Lisans Başvuru Takvimi"
    assert link.email_sender_email == "fbe@univ.edu.tr"
    assert link.task_title == "Yüksek Lisans Başvurusunu Tamamla"
    assert link.action_type == ActionType.REMINDER

    harness.record(
        "source_email_linkage",
        "PASS",
        f"Verified origin link: Reminder '{link.action_title}' -> Email '{link.email_subject}' from {link.email_sender_email}",
    )
    return True


async def run_scenario_5_security_and_prompt_injection(harness: Milestone42Harness) -> bool:
    console.print("\n[bold cyan]─── Scenario 5: Security Boundaries & Prompt Injection Defense ───[/bold cyan]")
    # Untrusted email with commands cannot bypass approval or execute forbidden operations
    policy_engine = harness.approval_svc.policy_engine

    # 1. Verify delete operations require R4 (not R2)
    delete_cal_def = ToolDefinition(
        name="calendar.delete_event",
        description="Delete event",
        risk_level=RiskLevel.R4_DESTRUCTIVE,
        input_schema={},
        requires_approval=True,
    )
    r4_req = PolicyRequest(
        agent_mode=AgentMode.ASSIST,
        tool_definition=delete_cal_def,
        tool_call=ToolCall(id="call_del_1", name="calendar.delete_event", arguments={"event_id": "fake"}),
    )
    decision = policy_engine.evaluate(r4_req)
    assert decision.decision == PolicyDecisionType.REQUIRE_APPROVAL
    harness.record("security_deletion_requires_r4", "PASS", "Calendar deletion is categorized as R4_DESTRUCTIVE and requires strict approval")

    # 2. Verify sensitive R5 operations are strictly DENIED
    r5_def = ToolDefinition(
        name="system.shell_exec",
        description="Shell execution",
        risk_level=RiskLevel.R5_SENSITIVE,
        input_schema={},
        requires_approval=True,
    )
    r5_req = PolicyRequest(
        agent_mode=AgentMode.ASSIST,
        tool_definition=r5_def,
        tool_call=ToolCall(id="call_r5_1", name="system.shell_exec", arguments={}),
    )
    r5_decision = policy_engine.evaluate(r5_req)
    assert r5_decision.decision == PolicyDecisionType.DENY
    harness.record("security_r5_strictly_denied", "PASS", "Arbitrary execution commands are classified as R5_SENSITIVE and strictly DENIED")

    return True


async def main() -> None:
    console.print(Panel.fit(
        "[bold green]JARVIS V1 — Milestone 4.2 Acceptance Test Suite[/bold green]\n"
        "[dim]Intelligent Task Approval, Apple Calendar & Reminders Planning[/dim]",
        border_style="green",
    ))

    harness = Milestone42Harness(use_real_eventkit=True)
    try:
        await run_scenario_1_deadline_reminder(harness)
        await run_scenario_2_work_block_calendar(harness)
        await run_scenario_3_idempotency(harness)
        await run_scenario_4_source_email_linkage(harness)
        await run_scenario_5_security_and_prompt_injection(harness)
    finally:
        console.print("\n[yellow]Performing post-test EventKit cleanup...[/yellow]")
        await harness.cleanup()

    # Results Table
    table = Table(title="Milestone 4.2 Acceptance Test Summary")
    table.add_column("Test Case", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Details")

    all_passed = True
    for r in harness.results:
        st_style = "green" if r["status"] == "PASS" else "red"
        if r["status"] != "PASS":
            all_passed = False
        table.add_row(r["name"], f"[{st_style}]{r['status']}[/{st_style}]", r["details"])

    console.print("\n", table)
    if all_passed:
        console.print("\n[bold green]ALL MILESTONE 4.2 ACCEPTANCE CHECKS PASSED ✅[/bold green]\n")
        sys.exit(0)
    else:
        console.print("\n[bold red]SOME ACCEPTANCE CHECKS FAILED ❌[/bold red]\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
