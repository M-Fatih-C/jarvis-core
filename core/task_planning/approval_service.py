"""Task Action Approval and Execution Service with PolicyEngine R2 enforcement and read-back verification."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from core.agent.approval_store import ApprovalStore
from core.agent.exceptions import ApprovalIntegrityError, JarvisError
from core.logging.setup import get_logger
from core.models.agent import AgentMode
from core.models.approval import ApprovalRequest
from core.models.tools import RiskLevel, ToolCall, ToolDefinition
from core.policy.engine import PolicyEngine
from core.policy.risk import PolicyDecisionType, PolicyRequest
from core.task_planning.planner import PlanningConflictError
from core.task_planning.schemas import (
    ActionDestination,
    ActionType,
    SourceEmailLinkResponse,
    TaskActionProposal,
)
from core.task_planning.state import TaskProposalStatus, transition_task_status
from integrations.gmail.storage import EmailStorage
from integrations.macos.client import MacBridgeClient

logger = get_logger("jarvis.task_planning.approval_service")


class DuplicateActionError(JarvisError):
    """Raised when attempting to execute an action that already exists or was executed."""
    pass


class ReadBackVerificationError(JarvisError):
    """Raised when EventKit read-back verification fails after save mutation."""
    pass


class TaskApprovalService:
    """Coordinates PolicyEngine R2 approval, idempotency, execution, and read-back verification."""

    def __init__(
        self,
        storage: EmailStorage,
        policy_engine: PolicyEngine | None = None,
        approval_store: ApprovalStore | None = None,
        bridge_client: MacBridgeClient | None = None,
    ) -> None:
        self.storage = storage
        self.policy_engine = policy_engine or PolicyEngine()
        self.approval_store = approval_store or ApprovalStore()
        self.bridge_client = bridge_client or MacBridgeClient()

    def _build_tool_call_for_action(self, action: TaskActionProposal) -> tuple[ToolDefinition, ToolCall]:
        """Construct exact ToolDefinition and ToolCall for an action."""
        tool_call_id = f"call_{action.action_id.hex[:12]}"

        if action.target_destination == ActionDestination.APPLE_REMINDERS:
            tool_name = "reminders.create"
            if not action.due_date:
                raise JarvisError(f"Reminder action '{action.title}' missing required due_date.")

            args: dict[str, Any] = {
                "title": action.title,
                "due_at": action.due_date.isoformat(),
            }
            if action.notes:
                args["notes"] = action.notes
            if action.target_list_id:
                args["list_id"] = action.target_list_id

            tool_def = ToolDefinition(
                name=tool_name,
                description="Create reminder in Apple Reminders",
                risk_level=RiskLevel.R2_WRITE,
                input_schema={},
                requires_approval=True,
            )
            return tool_def, ToolCall(id=tool_call_id, name=tool_name, arguments=args)

        elif action.target_destination == ActionDestination.APPLE_CALENDAR:
            tool_name = "calendar.create_event"
            if not action.start_time or not action.end_time:
                raise JarvisError(f"Calendar action '{action.title}' missing start_time or end_time.")

            args = {
                "title": action.title,
                "start": action.start_time.isoformat(),
                "end": action.end_time.isoformat(),
            }
            if action.target_calendar_id:
                args["calendar_id"] = action.target_calendar_id
            if action.notes:
                args["notes"] = action.notes

            tool_def = ToolDefinition(
                name=tool_name,
                description="Create event in Apple Calendar",
                risk_level=RiskLevel.R2_WRITE,
                input_schema={},
                requires_approval=True,
            )
            return tool_def, ToolCall(id=tool_call_id, name=tool_name, arguments=args)

        else:
            raise JarvisError(f"Unsupported destination: {action.target_destination.value}")

    async def prepare_action_approval(
        self,
        action: TaskActionProposal,
        agent_run_id: UUID | None = None,
        user_id: str | None = None,
    ) -> tuple[TaskActionProposal, ApprovalRequest]:
        """Evaluate action under PolicyEngine R2 and register for human approval.

        Enforces idempotency: rejects duplicate action proposals for same email/task/action_type.
        """
        previous_approval = self.approval_store.get(action.approval_id) if action.approval_id else None
        if action.external_id or action.status == TaskProposalStatus.EXECUTING or (previous_approval and previous_approval.consumed_at):
            raise DuplicateActionError("İşlem daha önce yürütüldü veya sonucu belirsiz. Mevcut kaydı kontrol edin.")
        # 1. Idempotency Check
        existing = await self.storage.get_task_action_by_idempotency_key(
            source_message_id=action.source_message_id,
            task_id=action.task_id,
            action_type=action.action_type.value,
        )
        if existing and existing.status in (TaskProposalStatus.EXECUTED, TaskProposalStatus.EXECUTING):
            raise DuplicateActionError(
                f"Mükerrer işlem engellendi: Bu görev için zaten {action.action_type.value} "
                f"kaydı oluşturulmuş (Durum: {existing.status.value}, ID: {existing.action_id})."
            )

        # 2. Build exact ToolCall
        tool_def, tool_call = self._build_tool_call_for_action(action)

        # 3. PolicyEngine R2 Evaluation
        policy_req = PolicyRequest(
            agent_mode=AgentMode.ASSIST,
            tool_definition=tool_def,
            tool_call=tool_call,
        )
        decision = self.policy_engine.evaluate(policy_req)

        if decision.decision == PolicyDecisionType.DENY:
            raise JarvisError(f"PolicyEngine denied proposed action '{tool_def.name}': {decision.reason}")

        # Strictly enforce human approval requirement
        if decision.decision != PolicyDecisionType.REQUIRE_APPROVAL:
            raise JarvisError(
                f"Security violation: Action '{tool_def.name}' must require R2 human approval, got {decision.decision.value}."
            )

        # 4. Register in ApprovalStore with Action Digest
        run_id = agent_run_id or action.task_id
        approval_req = self.approval_store.create(
            agent_run_id=run_id,
            tool_call=tool_call,
            user_id=user_id,
        )

        # 5. Transition to WAITING_APPROVAL
        next_status = transition_task_status(action.status, TaskProposalStatus.WAITING_APPROVAL)
        updated_action = action.model_copy(
            update={
                "approval_id": approval_req.id,
                "action_digest": approval_req.action_digest,
                "status": next_status,
            }
        )

        # 6. Save in SQLite
        await self.storage.save_task_action(updated_action)
        await self.storage.update_task_proposal_status(action.task_id, TaskProposalStatus.WAITING_APPROVAL)

        logger.info(
            "task_action_waiting_approval",
            action_id=str(updated_action.action_id),
            task_id=str(updated_action.task_id),
            approval_id=str(approval_req.id),
            action_digest=approval_req.action_digest,
        )
        return updated_action, approval_req

    async def approve_and_execute_action(self, action_id: UUID | str, *, user_id: str | None = None, action_digest: str | None = None, require_client_digest: bool = False) -> TaskActionProposal:
        """Approve, verify digest, execute via JarvisMacAgent, and perform read-back verification."""
        action = await self.storage.get_task_action(action_id)
        if not action:
            raise JarvisError(f"TaskActionProposal '{action_id}' not found.")

        # Idempotency guard: prevent duplicate execution
        if action.external_id or action.status in (TaskProposalStatus.EXECUTED, TaskProposalStatus.EXECUTING):
            raise DuplicateActionError(
                f"İşlem '{action.title}' zaten yürütülmüş (Durum: EXECUTED, External ID: {action.external_id})."
            )

        if not action.approval_id:
            raise JarvisError(f"Action '{action_id}' does not have an associated approval request.")

        # Reconstruct tool call to check integrity against digest
        tool_def, tool_call = self._build_tool_call_for_action(action)

        # 1. Approve if still pending
        app_req = self.approval_store.get(action.approval_id)
        if require_client_digest and (not app_req or not action_digest or action_digest != app_req.action_digest or app_req.user_id != user_id):
            raise ApprovalIntegrityError("Approval identity or action digest mismatch")
        if app_req and app_req.status.value == "pending":
            self.approval_store.approve(action.approval_id, current_tool_call=tool_call, user_id=user_id)

        # 2. Consume approval (validates one-time use and action digest integrity)
        self.approval_store.consume(action.approval_id, tool_call=tool_call, user_id=user_id)

        # 3. Transition to EXECUTING
        s1 = transition_task_status(action.status, TaskProposalStatus.APPROVED)
        s2 = transition_task_status(s1, TaskProposalStatus.EXECUTING)
        action = action.model_copy(update={"status": s2})
        await self.storage.save_task_action(action)
        await self.storage.update_task_proposal_status(action.task_id, TaskProposalStatus.EXECUTING)

        # 4. Pre-execution calendar conflict re-check (for calendar events)
        if action.target_destination == ActionDestination.APPLE_CALENDAR and action.start_time and action.end_time:
            try:
                check_res = await self.bridge_client.call(
                    "calendar.list_events",
                    {
                        "start": action.start_time.isoformat(),
                        "end": action.end_time.isoformat(),
                        "calendar_ids": [action.target_calendar_id] if action.target_calendar_id else None,
                        "limit": 10,
                    },
                )
                conflicts = check_res.get("events", [])
                if conflicts:
                    err_msg = (
                        f"Takvim çakışması: Onaylanan zaman aralığında ({action.start_time.isoformat()}) "
                        f"zaten '{conflicts[0].get('title')}' etkinliği mevcut."
                    )
                    action = action.model_copy(
                        update={
                            "status": TaskProposalStatus.FAILED,
                            "error_message": err_msg,
                        }
                    )
                    await self.storage.save_task_action(action)
                    await self.storage.update_task_proposal_status(action.task_id, TaskProposalStatus.FAILED)
                    raise PlanningConflictError(err_msg)
            except PlanningConflictError:
                raise
            except Exception as check_exc:
                raise PlanningConflictError("Takvim çakışması kontrol edilemedi; kayıt oluşturulmadı.") from check_exc

        # 5. Execute Mutation via Bridge
        try:
            call_res = await self.bridge_client.call(tool_call.name, tool_call.arguments)
        except Exception as exec_err:
            action = action.model_copy(
                update={
                    # The bridge may have saved before its reply was lost. Keep a
                    # durable executing marker; re-approval must not replay the write.
                    "status": TaskProposalStatus.EXECUTING,
                    "error_message": "Kayıt sonucu doğrulanamadı. Tekrar oluşturmadan önce Takvim/Hatırlatıcılar uygulamasını kontrol edin.",
                }
            )
            await self.storage.save_task_action(action)
            await self.storage.update_task_proposal_status(action.task_id, TaskProposalStatus.EXECUTING)
            raise JarvisError("EventKit sonucu belirsiz; aynı işlem otomatik tekrarlanmaz") from exec_err

        # Extract created external identifier
        record_id = ""
        if isinstance(call_res, dict):
            if "id" in call_res:
                record_id = call_res["id"]
            elif "event" in call_res and isinstance(call_res["event"], dict):
                record_id = call_res["event"].get("id", "")
            elif "reminder" in call_res and isinstance(call_res["reminder"], dict):
                record_id = call_res["reminder"].get("id", "")

        # Persist the actual ID before read-back so a failure cannot cause a duplicate.
        action = action.model_copy(update={"external_id": record_id or None})
        await self.storage.save_task_action(action)
        try:
            if not record_id:
                raise ReadBackVerificationError("EventKit returned no record ID; verify before retrying")
            await self._verify_read_back(action=action, external_id=record_id)
        except ReadBackVerificationError as exc:
            # Missing ID is also an uncertain mutation; retain its durable marker.
            failed_status = TaskProposalStatus.FAILED if record_id else TaskProposalStatus.EXECUTING
            action = action.model_copy(update={"status": failed_status, "error_message": str(exc)})
            await self.storage.save_task_action(action)
            await self.storage.update_task_proposal_status(action.task_id, failed_status)
            raise

        # 7. Transition to EXECUTED
        now_dt = datetime.now(timezone.utc)
        executed_status = transition_task_status(action.status, TaskProposalStatus.EXECUTED)
        final_action = action.model_copy(
            update={
                "status": executed_status,
                "external_id": record_id,
                "executed_at": now_dt,
                "error_message": None,
            }
        )
        await self.storage.save_task_action(final_action)
        await self.storage.update_task_proposal_status(action.task_id, TaskProposalStatus.EXECUTED)

        logger.info(
            "task_action_executed_successfully",
            action_id=str(final_action.action_id),
            task_id=str(final_action.task_id),
            external_id=record_id,
            action_type=final_action.action_type.value,
        )
        return final_action

    async def _verify_read_back(self, action: TaskActionProposal, external_id: str) -> None:
        """Require the exact EventKit ID, title and dates; missing/failed reads fail closed."""
        try:
            if action.target_destination == ActionDestination.APPLE_CALENDAR:
                res = await self.bridge_client.call("calendar.get_event", {"event_id": external_id})
                dates = (("start", action.start_time), ("end", action.end_time))
            else:
                res = await self.bridge_client.call("reminders.get", {"reminder_id": external_id})
                dates = (("due_at", action.due_date),)
            if not isinstance(res, dict) or res.get("id") != external_id or res.get("title") != action.title:
                raise ReadBackVerificationError("Record not found in Apple Reminders/Calendar with matching ID and title")
            for field, expected in dates:
                if expected and (not isinstance(res.get(field), str) or datetime.fromisoformat(res[field]) != expected):
                    raise ReadBackVerificationError("EventKit record date does not match the approved action")
        except ReadBackVerificationError:
            raise
        except Exception as exc:
            raise ReadBackVerificationError("EventKit read-back unavailable; verify before retrying") from exc

    async def dismiss_action(self, action_id: UUID | str, *, user_id: str | None = None) -> TaskActionProposal:
        """Dismiss a planned action and update parent proposal if appropriate."""
        action = await self.storage.get_task_action(action_id)
        if not action:
            raise JarvisError(f"TaskActionProposal '{action_id}' not found.")

        if action.approval_id:
            req = self.approval_store.get(action.approval_id)
            if req and req.status.value == "pending":
                self.approval_store.reject(action.approval_id, user_id=user_id)
        next_status = transition_task_status(action.status, TaskProposalStatus.DISMISSED)
        updated = action.model_copy(update={"status": next_status})
        await self.storage.save_task_action(updated)

        # Check if all actions for this task are dismissed
        sibling_actions = await self.storage.get_task_actions_for_task(action.task_id)
        if all(a.status == TaskProposalStatus.DISMISSED for a in sibling_actions):
            await self.storage.update_task_proposal_status(action.task_id, TaskProposalStatus.DISMISSED)

        return updated

    async def get_source_email_for_external_record(self, external_id: str) -> SourceEmailLinkResponse | None:
        """Query origin email for an EventKit event or reminder."""
        row_dict = await self.storage.get_source_email_by_external_id(external_id)
        if not row_dict:
            return None

        rec_at = row_dict["email_received_at"]
        if isinstance(rec_at, str):
            rec_at = datetime.fromisoformat(rec_at)

        exec_at = row_dict["executed_at"]
        if isinstance(exec_at, str):
            exec_at = datetime.fromisoformat(exec_at)

        return SourceEmailLinkResponse(
            external_id=row_dict["external_id"],
            action_type=ActionType(row_dict["action_type"]),
            action_title=row_dict["action_title"],
            task_id=UUID(row_dict["task_id"]),
            task_title=row_dict["task_title"],
            source_message_id=row_dict["source_message_id"],
            email_subject=row_dict["email_subject"],
            email_sender=row_dict["email_sender"],
            email_sender_email=row_dict["email_sender_email"],
            email_received_at=rec_at,
            email_preview=row_dict["email_preview"],
            executed_at=exec_at,
        )
