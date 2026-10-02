#!/usr/bin/env python3
"""CLI utility for Milestone 4.2: Task Proposals, Calendar & Reminders Planning, and Approvals."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import json
import sys
from uuid import UUID

from core.config.settings import get_settings
from core.email_analysis.schemas import TaskProposal
from core.task_planning.approval_service import TaskApprovalService
from core.task_planning.calendar_manager import CalendarTargetManager
from core.task_planning.planner import TaskPlanner
from core.task_planning.schemas import (
    ActionDestination,
    CalendarCategory,
    TaskActionProposal,
)
from core.task_planning.state import TaskProposalStatus
from integrations.gmail.storage import EmailStorage
from integrations.macos.client import MacBridgeClient


async def cmd_list_proposals(storage: EmailStorage, status: str | None = None) -> None:
    proposals = await storage.get_task_proposals(status=status)
    print(f"\nFound {len(proposals)} Task Proposals (Filter: {status or 'ALL'}):")
    print("-" * 80)
    for p in proposals:
        status_str = p.get("status")
        status_val = status_str.value if hasattr(status_str, "value") else str(status_str)
        print(f"ID:       {p['task_id']}")
        print(f"Title:    {p['title']}")
        print(f"Category: {p['category']} | Priority: {p['priority']} | Status: {status_val}")
        print(f"Deadline: {p.get('deadline') or 'None'} ({p.get('deadline_confidence')})")
        print(f"Action:   {p.get('proposed_action')}")
        print("-" * 80)


async def cmd_show(storage: EmailStorage, task_id: UUID) -> None:
    p = await storage.get_task_proposal(task_id)
    if not p:
        print(f"Task proposal '{task_id}' not found.")
        return

    email = await storage.get_email(p["source_message_id"])
    actions = await storage.get_task_actions_for_task(task_id)

    status_str = p.get("status")
    status_val = status_str.value if hasattr(status_str, "value") else str(status_str)

    print("\n" + "=" * 80)
    print(f"TASK PROPOSAL: {p['title']}")
    print("=" * 80)
    print(f"Task ID:      {p['task_id']}")
    print(f"Status:       {status_val}")
    print(f"Priority:     {p['priority']}")
    print(f"Category:     {p['category']}")
    print(f"Deadline:     {p.get('deadline')} ({p.get('deadline_confidence')})")
    print(f"Description:  {p['description']}")
    print(f"Action:       {p['proposed_action']}")
    print("-" * 80)
    print("SOURCE EMAIL METADATA:")
    if email:
        print(f"Subject:      {email.subject}")
        print(f"Sender:       {email.sender}")
        print(f"Date:         {email.received_at.isoformat()}")
        print(f"Preview:      {email.body_preview}")
    else:
        print(f"Message ID:   {p['source_message_id']} (Not in local email cache)")
    print("-" * 80)
    print(f"PLANNED ACTIONS ({len(actions)}):")
    for a in actions:
        print(f"  • Action ID:    {a.action_id}")
        print(f"    Type:         {a.action_type.value} -> {a.target_destination.value}")
        print(f"    Title:        {a.title}")
        print(f"    Status:       {a.status.value}")
        if a.due_date:
            print(f"    Due Date:     {a.due_date.isoformat()}")
        if a.start_time:
            print(f"    Slot:         {a.start_time.isoformat()} - {a.end_time.isoformat() if a.end_time else ''}")
        if a.approval_id:
            print(f"    Approval ID:  {a.approval_id}")
            print(f"    Digest:       {a.action_digest}")
        if a.external_id:
            print(f"    External ID:  {a.external_id} (Executed at: {a.executed_at})")
    print("=" * 80 + "\n")


async def cmd_plan_reminder(
    storage: EmailStorage,
    planner: TaskPlanner,
    task_id: UUID,
    due_date_str: str | None = None,
) -> None:
    p = await storage.get_task_proposal(task_id)
    if not p:
        print(f"Task proposal '{task_id}' not found.")
        return

    proposal = TaskProposal(**p)
    custom_due = datetime.fromisoformat(due_date_str) if due_date_str else None

    action = planner.plan_reminder_for_deadline(proposal, custom_due_date=custom_due)
    await storage.save_task_action(action)

    print("\nPlanned Reminder Action:")
    print(f"Action ID:    {action.action_id}")
    print(f"Destination:  {action.target_destination.value}")
    print(f"Title:        {action.title}")
    print(f"Due Date:     {action.due_date.isoformat() if action.due_date else 'None'}")
    print(f"Status:       {action.status.value}")
    print("Run `request-approval` to submit to PolicyEngine R2.\n")


async def cmd_plan_work_block(
    storage: EmailStorage,
    planner: TaskPlanner,
    task_id: UUID,
    duration: int,
    calendar_id: str | None = None,
) -> None:
    p = await storage.get_task_proposal(task_id)
    if not p:
        print(f"Task proposal '{task_id}' not found.")
        return

    proposal = TaskProposal(**p)
    action, slots = await planner.plan_work_block(
        proposal=proposal,
        duration_minutes=duration,
        target_calendar_id=calendar_id,
    )
    await storage.save_task_action(action)

    print(f"\nCalculated {len(slots)} Conflict-Free Slots:")
    for i, s in enumerate(slots[:5]):
        print(f"  [{i}] {s.start.strftime('%Y-%m-%d %H:%M')} - {s.end.strftime('%H:%M')} (Score: {s.score}) | {s.reason}")

    print("\nSelected Default Action:")
    print(f"Action ID:    {action.action_id}")
    print(f"Destination:  {action.target_destination.value}")
    print(f"Title:        {action.title}")
    print(f"Window:       {action.start_time.isoformat() if action.start_time else ''} - {action.end_time.isoformat() if action.end_time else ''}")
    print(f"Status:       {action.status.value}")
    print("Run `request-approval` to submit to PolicyEngine R2.\n")


async def cmd_request_approval(approval_svc: TaskApprovalService, storage: EmailStorage, action_id: UUID) -> None:
    action = await storage.get_task_action(action_id)
    if not action:
        print(f"Action '{action_id}' not found.")
        return

    updated_action, approval_req = await approval_svc.prepare_action_approval(action)
    print("\nAction Submitted to PolicyEngine R2:")
    print(f"Action ID:     {updated_action.action_id}")
    print(f"Status:        {updated_action.status.value}")
    print(f"Approval ID:   {approval_req.id}")
    print(f"Action Digest: {approval_req.action_digest}")
    print(f"Expires At:    {approval_req.expires_at.isoformat()}")
    print("Run `approve` to execute and verify by read-back.\n")


async def cmd_approve(approval_svc: TaskApprovalService, action_id: UUID) -> None:
    final_action = await approval_svc.approve_and_execute_action(action_id)
    print("\nAction Approved and Executed Successfully:")
    print(f"Action ID:    {final_action.action_id}")
    print(f"Status:       {final_action.status.value}")
    print(f"External ID:  {final_action.external_id}")
    print(f"Executed At:  {final_action.executed_at.isoformat() if final_action.executed_at else ''}")
    print("EventKit Read-back: CONFIRMED ✅\n")


async def cmd_dismiss(approval_svc: TaskApprovalService, action_id: UUID) -> None:
    updated = await approval_svc.dismiss_action(action_id)
    print(f"\nAction '{action_id}' dismissed. Status: {updated.status.value}\n")


async def cmd_list_calendars(calendar_mgr: CalendarTargetManager) -> None:
    cals = await calendar_mgr.list_writable_calendars()
    pref, warn = await calendar_mgr.get_preferred_calendar()

    print(f"\nFound {len(cals)} Writable Apple Calendars:")
    print("-" * 80)
    for c in cals:
        is_pref = (pref and pref.id == c.id)
        flag = " [PREFERRED iCLOUD]" if is_pref else (" [ON MY MAC]" if c.is_on_my_mac else "")
        print(f"ID:      {c.id}")
        print(f"Title:   {c.title}{flag}")
        print(f"Source:  {c.source_title or 'Unknown'} (Type: {c.source_type or 'Unknown'})")
        print("-" * 80)

    if warn:
        print(f"WARNING: {warn}\n")


async def cmd_source_email(approval_svc: TaskApprovalService, external_id: str) -> None:
    link = await approval_svc.get_source_email_for_external_record(external_id)
    if not link:
        print(f"\nNo source email found for external record '{external_id}'.\n")
        return

    print("\n" + "=" * 80)
    print(f"ORIGIN EMAIL FOR RECORD: {external_id}")
    print("=" * 80)
    print(f"Action Title:   {link.action_title} ({link.action_type.value})")
    print(f"Task Title:     {link.task_title} (ID: {link.task_id})")
    print(f"Executed At:    {link.executed_at}")
    print("-" * 80)
    print(f"Email Subject:  {link.email_subject}")
    print(f"Sender:         {link.email_sender} <{link.email_sender_email}>")
    print(f"Received At:    {link.email_received_at.isoformat()}")
    print(f"Message ID:     {link.source_message_id}")
    print(f"Preview:        {link.email_preview}")
    print("=" * 80 + "\n")


async def main_async() -> None:
    parser = argparse.ArgumentParser(description="Jarvis Milestone 4.2 Task Approval & Planning CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list-proposals
    lp = subparsers.add_parser("list-proposals", help="List extracted task proposals")
    lp.add_argument("--status", choices=["PROPOSED", "WAITING_APPROVAL", "APPROVED", "EXECUTING", "EXECUTED", "DISMISSED", "FAILED"], default=None)

    # show
    sh = subparsers.add_parser("show", help="Show task proposal and planned actions")
    sh.add_argument("task_id", type=UUID)

    # plan-reminder
    pr = subparsers.add_parser("plan-reminder", help="Plan Apple Reminder for a deadline")
    pr.add_argument("task_id", type=UUID)
    pr.add_argument("--due", type=str, default=None, help="Explicit due datetime (ISO 8601)")

    # plan-work-block
    pw = subparsers.add_parser("plan-work-block", help="Plan conflict-free work block on Apple Calendar")
    pw.add_argument("task_id", type=UUID)
    pw.add_argument("--duration", type=int, default=120, help="Duration in minutes")
    pw.add_argument("--calendar-id", type=str, default=None)

    # request-approval
    ra = subparsers.add_parser("request-approval", help="Submit action to PolicyEngine R2")
    ra.add_argument("action_id", type=UUID)

    # approve
    ap = subparsers.add_parser("approve", help="Approve and execute action via EventKit")
    ap.add_argument("action_id", type=UUID)

    # dismiss
    dis = subparsers.add_parser("dismiss", help="Dismiss an action")
    dis.add_argument("action_id", type=UUID)

    # list-calendars
    subparsers.add_parser("list-calendars", help="List writable calendars and iCloud status")

    # source-email
    se = subparsers.add_parser("source-email", help="Find source email for an EventKit record")
    se.add_argument("external_id", type=str)

    args = parser.parse_args()

    settings = get_settings()
    storage = EmailStorage(db_path=settings.email_db_path)
    bridge_client = MacBridgeClient()
    calendar_mgr = CalendarTargetManager(bridge_client=bridge_client, settings=settings)
    planner = TaskPlanner(bridge_client=bridge_client, buffer_minutes=settings.planning_buffer_minutes)
    approval_svc = TaskApprovalService(storage=storage, bridge_client=bridge_client)

    try:
        if args.command == "list-proposals":
            await cmd_list_proposals(storage, args.status)
        elif args.command == "show":
            await cmd_show(storage, args.task_id)
        elif args.command == "plan-reminder":
            await cmd_plan_reminder(storage, planner, args.task_id, args.due)
        elif args.command == "plan-work-block":
            await cmd_plan_work_block(storage, planner, args.task_id, args.duration, args.calendar_id)
        elif args.command == "request-approval":
            await cmd_request_approval(approval_svc, storage, args.action_id)
        elif args.command == "approve":
            await cmd_approve(approval_svc, args.action_id)
        elif args.command == "dismiss":
            await cmd_dismiss(approval_svc, args.action_id)
        elif args.command == "list-calendars":
            await cmd_list_calendars(calendar_mgr)
        elif args.command == "source-email":
            await cmd_source_email(approval_svc, args.external_id)
    finally:
        storage.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
