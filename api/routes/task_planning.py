"""FastAPI routes for intelligent task planning, approval flow, and Apple EventKit execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.dependencies import (
    get_calendar_manager,
    get_email_storage,
    get_memory_service,
    get_task_approval_service,
    get_task_planner,
)
from core.email_analysis.schemas import TaskProposal
from core.task_planning.approval_service import DuplicateActionError, TaskApprovalService
from core.task_planning.calendar_manager import CalendarTargetManager
from core.task_planning.planner import AmbiguousDeadlineError, PlanningConflictError, TaskPlanner
from core.task_planning.schemas import (
    ActionDestination,
    CalendarCategory,
    CalendarTargetInfo,
    SourceEmailLinkResponse,
    TaskActionProposal,
    TaskProposalDetailResponse,
    TimeSlotProposal,
)
from core.task_planning.state import TaskProposalStatus
from integrations.gmail.storage import EmailStorage

router = APIRouter(prefix="/api/v1/tasks", tags=["Task Planning & Approval"])


class PlanReminderRequest(BaseModel):
    """Payload to plan an Apple Reminder for a deadline."""
    custom_due_date: datetime | None = Field(default=None, description="Optional explicit due date")
    target_list_id: str | None = Field(default=None, description="Optional target Apple Reminders list ID")


class PlanWorkBlockRequest(BaseModel):
    """Payload to plan a focused work block."""
    duration_minutes: int = Field(default=120, ge=15, le=480, description="Duration in minutes (e.g. 120)")
    target_calendar_id: str | None = Field(default=None, description="Optional target Apple Calendar ID")
    logical_category: CalendarCategory = Field(default=CalendarCategory.WORK, description="WORK, EDUCATION, PERSONAL, PROJECT")
    selected_slot_index: int = Field(default=0, ge=0, description="Index of chosen candidate slot")


class PlanWorkBlockResponse(BaseModel):
    """Response containing proposed action and all calculated candidate slots."""
    action: TaskActionProposal
    candidate_slots: list[TimeSlotProposal]


class CustomizeActionRequest(BaseModel):
    """Payload for user modifications prior to approval."""
    title: str | None = None
    notes: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    due_date: datetime | None = None
    target_destination: ActionDestination | None = None
    target_calendar_id: str | None = None
    target_list_id: str | None = None
    logical_category: CalendarCategory | None = None


class CalendarsResponse(BaseModel):
    """List of available calendars with preferred iCloud indication."""
    calendars: list[CalendarTargetInfo]
    preferred_calendar_id: str | None = None
    warning: str | None = None


@router.get("/proposals", response_model=list[dict[str, Any]])
async def list_proposals(
    status_filter: str | None = Query(default=None, alias="status"),
    storage: EmailStorage = Depends(get_email_storage),
) -> list[dict[str, Any]]:
    """List extracted task proposals with optional status filter."""
    return await storage.get_task_proposals(status=status_filter)


@router.get("/proposals/{task_id}", response_model=TaskProposalDetailResponse)
async def get_proposal_details(
    task_id: UUID,
    storage: EmailStorage = Depends(get_email_storage),
) -> TaskProposalDetailResponse:
    """Retrieve detailed view of a task proposal, its source email context, and planned actions."""
    task_row = await storage.get_task_proposal(task_id)
    if not task_row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task proposal '{task_id}' not found.",
        )

    # Fetch source email
    email = await storage.get_email(task_row["source_message_id"])
    actions = await storage.get_task_actions_for_task(task_id)

    proposal = TaskProposal(**task_row)
    return TaskProposalDetailResponse(
        task=proposal,
        source_email_subject=email.subject if email else None,
        source_email_sender=email.sender if email else None,
        source_email_date=email.received_at if email else None,
        source_email_preview=email.body_preview if email else None,
        actions=actions,
    )


@router.post("/proposals/{task_id}/plan-reminder", response_model=TaskActionProposal)
async def plan_reminder(
    task_id: UUID,
    req: PlanReminderRequest,
    storage: EmailStorage = Depends(get_email_storage),
    planner: TaskPlanner = Depends(get_task_planner),
) -> TaskActionProposal:
    """Plan an Apple Reminder action for an extracted task with a deadline."""
    task_row = await storage.get_task_proposal(task_id)
    if not task_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")

    proposal = TaskProposal(**task_row)
    try:
        action = planner.plan_reminder_for_deadline(
            proposal=proposal,
            custom_due_date=req.custom_due_date,
            target_list_id=req.target_list_id,
        )
    except AmbiguousDeadlineError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    await storage.save_task_action(action)
    return action


@router.post("/proposals/{task_id}/plan-work-block", response_model=PlanWorkBlockResponse)
async def plan_work_block(
    task_id: UUID,
    req: PlanWorkBlockRequest,
    storage: EmailStorage = Depends(get_email_storage),
    planner: TaskPlanner = Depends(get_task_planner),
    calendar_mgr: CalendarTargetManager = Depends(get_calendar_manager),
    memory_service: Any = Depends(get_memory_service),
) -> PlanWorkBlockResponse:
    """Compute conflict-free calendar slots and propose a work block."""
    task_row = await storage.get_task_proposal(task_id)
    if not task_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")

    proposal = TaskProposal(**task_row)

    # Determine target calendar: prefer explicit target or preferred iCloud
    target_cal_id = req.target_calendar_id
    if not target_cal_id:
        pref_cal, warn = await calendar_mgr.get_preferred_calendar()
        if pref_cal:
            target_cal_id = pref_cal.id

    try:
        action, candidate_slots = await planner.plan_work_block(
            proposal=proposal,
            duration_minutes=req.duration_minutes,
            target_calendar_id=target_cal_id,
            logical_category=req.logical_category,
            memory_service=memory_service,
            selected_slot_index=req.selected_slot_index,
        )
    except PlanningConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    await storage.save_task_action(action)
    return PlanWorkBlockResponse(action=action, candidate_slots=candidate_slots)


@router.patch("/actions/{action_id}/customize", response_model=TaskActionProposal)
async def customize_action(
    action_id: UUID,
    req: CustomizeActionRequest,
    storage: EmailStorage = Depends(get_email_storage),
) -> TaskActionProposal:
    """Customize details of a planned action (title, time, destination) prior to approval."""
    action = await storage.get_task_action(action_id)
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found.")

    if action.status not in (TaskProposalStatus.PROPOSED, TaskProposalStatus.WAITING_APPROVAL):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot customize action in state '{action.status.value}'.",
        )

    updates: dict[str, Any] = {}
    if req.title is not None:
        updates["title"] = req.title
    if req.notes is not None:
        updates["notes"] = req.notes
    if req.start_time is not None:
        updates["start_time"] = req.start_time
    if req.end_time is not None:
        updates["end_time"] = req.end_time
    if req.due_date is not None:
        updates["due_date"] = req.due_date
    if req.target_destination is not None:
        updates["target_destination"] = req.target_destination
    if req.target_calendar_id is not None:
        updates["target_calendar_id"] = req.target_calendar_id
    if req.target_list_id is not None:
        updates["target_list_id"] = req.target_list_id
    if req.logical_category is not None:
        updates["logical_category"] = req.logical_category

    # If action was waiting approval, return to proposed so new digest is required
    updates["status"] = TaskProposalStatus.PROPOSED
    updates["approval_id"] = None
    updates["action_digest"] = None

    updated_action = action.model_copy(update=updates)
    await storage.save_task_action(updated_action)
    return updated_action


@router.post("/actions/{action_id}/request-approval")
async def request_approval(
    action_id: UUID,
    approval_svc: TaskApprovalService = Depends(get_task_approval_service),
    storage: EmailStorage = Depends(get_email_storage),
) -> dict[str, Any]:
    """Submit action to PolicyEngine R2 and register for human approval."""
    action = await storage.get_task_action(action_id)
    if not action:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found.")

    try:
        updated_action, approval_req = await approval_svc.prepare_action_approval(action)
        return {
            "status": "waiting_approval",
            "action_id": str(updated_action.action_id),
            "approval_id": str(approval_req.id),
            "action_digest": approval_req.action_digest,
            "expires_at": approval_req.expires_at.isoformat(),
        }
    except DuplicateActionError as dup_err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(dup_err))


@router.post("/actions/{action_id}/approve", response_model=TaskActionProposal)
async def approve_and_execute(
    action_id: UUID,
    approval_svc: TaskApprovalService = Depends(get_task_approval_service),
) -> TaskActionProposal:
    """Approve and execute action via EventKit with read-back verification."""
    try:
        executed_action = await approval_svc.approve_and_execute_action(action_id)
        return executed_action
    except DuplicateActionError as dup_err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(dup_err))
    except PlanningConflictError as conf_err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(conf_err))


@router.post("/actions/{action_id}/dismiss", response_model=TaskActionProposal)
async def dismiss_action(
    action_id: UUID,
    approval_svc: TaskApprovalService = Depends(get_task_approval_service),
) -> TaskActionProposal:
    """Dismiss a planned action."""
    return await approval_svc.dismiss_action(action_id)


@router.get("/calendars", response_model=CalendarsResponse)
async def list_calendars(
    calendar_mgr: CalendarTargetManager = Depends(get_calendar_manager),
) -> CalendarsResponse:
    """List writable Apple Calendars and indicate preferred iCloud calendar."""
    writable_cals = await calendar_mgr.list_writable_calendars()
    pref_cal, warn = await calendar_mgr.get_preferred_calendar()

    return CalendarsResponse(
        calendars=writable_cals,
        preferred_calendar_id=pref_cal.id if pref_cal else None,
        warning=warn,
    )


@router.get("/source-email/{external_id}", response_model=SourceEmailLinkResponse)
async def get_source_email(
    external_id: str,
    approval_svc: TaskApprovalService = Depends(get_task_approval_service),
) -> SourceEmailLinkResponse:
    """Query bidirectional link: find which email generated an EventKit reminder or calendar event."""
    link = await approval_svc.get_source_email_for_external_record(external_id)
    if not link:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No source email link found for external record '{external_id}'.",
        )
    return link
