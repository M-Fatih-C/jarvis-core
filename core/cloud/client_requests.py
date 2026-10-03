"""Owner-only phone screen operations over the existing authenticated queue.

Planning only creates local proposals. EventKit writes must arrive as a separate
approval_response with the exact server-issued approval ID and action digest.
"""
from datetime import datetime
from uuid import UUID

from core.email_analysis.schemas import TaskProposal
from core.models.agent import AgentMode
from core.models.approval import ApprovalStatus
from core.task_planning.schemas import TaskProposalDetailResponse
from core.task_planning.state import TaskProposalStatus


class ClientRequests:
    def __init__(self, *, owner, storage, planner, calendar_manager, approval_service, memory=None, mode="assist"):
        self.owner = owner
        self.storage = storage
        self.planner = planner
        self.calendars = calendar_manager
        self.approvals = approval_service
        self.memory = memory
        self.mode = AgentMode(mode)

    async def handle(self, name, arguments, user_id):
        if not user_id or user_id != self.owner:
            raise PermissionError("Authenticated owner required")
        if name == "client.emails":
            return {"items": await self.storage.list_email_summaries(limit=50)}
        if name == "client.proposals":
            rows = (await self.storage.get_task_proposals())[:50]
            details = [await self.detail(UUID(row["task_id"])) for row in rows]
            return {"items": rows, "details": details}
        if name == "client.proposal":
            return await self.detail(UUID(arguments["task_id"]))
        if self.mode == AgentMode.OBSERVE:
            raise PermissionError("Observe mode does not permit planning changes")
        if name in ("client.plan_reminder", "client.plan_work_block"):
            task_id = UUID(arguments["task_id"])
            row = await self.storage.get_task_proposal(task_id)
            if not row:
                raise ValueError("Görev bulunamadı")
            proposal = TaskProposal(**row)
            if name == "client.plan_reminder":
                due = arguments.get("due")
                due = datetime.fromisoformat(due) if due else None
                if due and due.tzinfo is None:
                    raise ValueError("Saat dilimi gerekli")
                action = self.planner.plan_reminder_for_deadline(proposal, custom_due_date=due)
            else:
                duration = arguments.get("duration", 120)
                if type(duration) is not int or not 15 <= duration <= 480:
                    raise ValueError("Süre 15–480 dakika olmalı")
                calendar, _ = await self.calendars.get_preferred_calendar()
                action, _ = await self.planner.plan_work_block(proposal, duration_minutes=duration,
                    target_calendar_id=calendar.id if calendar else None, memory_service=self.memory)
            previous = await self.storage.get_task_action_by_idempotency_key(
                source_message_id=action.source_message_id, task_id=action.task_id, action_type=action.action_type.value)
            # Repeated taps cannot create duplicate planned actions, especially after an uncertain write.
            if previous and previous.status not in (TaskProposalStatus.DISMISSED,):
                return previous.model_dump(mode="json")
            await self.storage.save_task_action(action)
            return action.model_dump(mode="json")
        if name == "client.request_approval":
            action = await self.storage.get_task_action(UUID(arguments["action_id"]))
            if not action:
                raise ValueError("İşlem bulunamadı")
            if action.external_id or action.status in (TaskProposalStatus.EXECUTED, TaskProposalStatus.EXECUTING):
                raise ValueError("İşlem zaten kaydedildi veya sonucu kontrol edilmeli; tekrar yürütülmez")
            current = self.approvals.approval_store.get(action.approval_id) if action.approval_id else None
            if current and current.status == ApprovalStatus.PENDING and current.user_id == user_id:
                return action.model_dump(mode="json")
            updated, _ = await self.approvals.prepare_action_approval(action, user_id=user_id)
            return updated.model_dump(mode="json")
        if name == "client.dismiss":
            action = await self.approvals.dismiss_action(UUID(arguments["action_id"]), user_id=user_id)
            return action.model_dump(mode="json")
        raise ValueError("Unsupported phone screen request")

    async def detail(self, task_id):
        row = await self.storage.get_task_proposal(task_id)
        if not row:
            raise ValueError("Görev bulunamadı")
        email = await self.storage.get_email(row["source_message_id"])
        return TaskProposalDetailResponse(task=TaskProposal(**row),
            source_email_subject=email.subject if email else None,
            source_email_sender=email.sender if email else None,
            source_email_date=email.received_at if email else None,
            source_email_preview=email.body_preview if email else None,
            actions=await self.storage.get_task_actions_for_task(task_id)).model_dump(mode="json")

    async def approve(self, payload, user_id):
        if user_id != self.owner or self.mode == AgentMode.OBSERVE:
            raise PermissionError("Authenticated owner in assist mode required")
        action = await self.storage.get_task_action(UUID(payload["task_action_id"]))
        if not action or str(action.approval_id) != payload.get("approval_id"):
            raise ValueError("Approval does not belong to this task action")
        if payload.get("decision") != "approved":
            raise ValueError("Only an explicit approval can execute a task action")
        result = await self.approvals.approve_and_execute_action(action.action_id, user_id=user_id,
            action_digest=payload.get("action_digest"), require_client_digest=True)
        return result.model_dump(mode="json")
