"""Outbound polling command worker for safe transactional cloud execution with verified human approval."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from core.agent.approval_store import ApprovalStore
from core.agent.exceptions import (
    ApprovalExpiredError,
    ApprovalIntegrityError,
    JarvisError,
    PolicyDeniedError,
)
from core.agent.runtime import AgentRuntime
from core.agent.state_machine import AgentState
from core.cloud.models import CloudCommand, CommandStatus
from core.cloud.repositories import CommandConflictError, CommandRepository
from core.config.settings import Settings, get_settings
from core.logging.setup import get_logger
from core.models.agent import AgentMode
from core.models.approval import ApprovalStatus, compute_action_digest
from core.models.tools import RiskLevel, ToolCall
from core.policy.engine import PolicyEngine
from core.policy.risk import PolicyDecisionType, PolicyRequest
from core.tools.executor import ToolExecutor

logger = get_logger("jarvis.cloud.worker")


class CommandWorker:
    """Outbound background worker polling Firestore command queue to process agent actions."""

    def __init__(
        self,
        command_repo: CommandRepository,
        runtime: AgentRuntime,
        tool_executor: ToolExecutor | None = None,
        settings: Settings | None = None,
        task_approval_service: Any = None,
        client_requests: Any = None,
    ) -> None:
        self._repo = command_repo
        self._runtime = runtime
        self._executor = tool_executor or runtime._executor
        self._settings = settings or get_settings()
        self._task_approval_service = task_approval_service
        self._client_requests = client_requests
        self._poll_task: asyncio.Task[None] | None = None
        self._running = False

    async def _verify_and_execute_tool(self, cmd: CloudCommand) -> tuple[bool, Any, str | None]:
        """Strictly verify policy and server-side human approval before tool execution."""
        # 1. Lookup Tool Definition
        try:
            tool = self._runtime._tools.get(cmd.name)
            tool_def = tool.definition
        except Exception:
            return False, None, f"ToolNotFoundError: Tool '{cmd.name}' is not registered."

        agent_mode = AgentMode(self._runtime._settings.default_agent_mode)

        # 2. Strict Policy Checks
        # Invariant: R5_SENSITIVE is unconditionally denied
        if tool_def.risk_level == RiskLevel.R5_SENSITIVE:
            return False, None, f"PolicyDenied: Tool '{cmd.name}' is classified as R5_SENSITIVE and cannot be executed."

        # Invariant: OBSERVE mode strictly denies all non-read actions
        if agent_mode == AgentMode.OBSERVE and tool_def.risk_level != RiskLevel.R0_READ:
            return False, None, f"PolicyDenied: Agent is operating in OBSERVE mode; tool '{cmd.name}' is denied."

        # 3. Read-only tools (R0_READ) can execute directly
        if tool_def.risk_level == RiskLevel.R0_READ and not tool_def.requires_approval:
            tool_call = ToolCall(
                id=cmd.idempotency_key,
                name=cmd.name,
                arguments=cmd.payload.get("arguments", {}),
                requested_at=datetime.now(timezone.utc),
            )
            tool_res = await self._executor.execute_tool_call(tool_call, agent_mode=agent_mode, is_approved=False)
            return tool_res.success, tool_res.data, tool_res.error

        # 4. Mutating / Write tools (R2, R3, R4) require verified server-side human approval
        # CLIENT-SUPPLIED is_approved=True MUST NEVER BE ACCEPTED AS EVIDENCE OF APPROVAL
        approval_id_str = cmd.payload.get("approval_id")
        if not approval_id_str:
            return False, None, (
                f"ApprovalRequired: Tool '{cmd.name}' (RiskLevel: {tool_def.risk_level.name}) requires "
                "verified server-side approval. Client-supplied is_approved=true is rejected."
            )

        try:
            approval_id = UUID(str(approval_id_str))
        except ValueError:
            return False, None, f"InvalidApprovalId: '{approval_id_str}' is not a valid UUID."

        # Look up trusted server-side approval record
        approval_req = self._runtime.approval_store.get(approval_id)
        if not approval_req:
            return False, None, f"ApprovalNotFound: No approval record found on server for ID '{approval_id}'."

        # Verify Approval Expiration
        now = datetime.now(timezone.utc)
        if approval_req.status == ApprovalStatus.EXPIRED or now > approval_req.expires_at:
            return False, None, f"ApprovalExpired: Approval '{approval_id}' has expired."

        # Security invariant: The worker must NEVER promote a PENDING approval to APPROVED solely because
        # a tool-execution command references it. Require a genuinely approved, unexpired and unconsumed record.
        if approval_req.status != ApprovalStatus.APPROVED:
            return False, None, (
                f"ApprovalNotGranted: Approval '{approval_id}' is in status '{approval_req.status.value}', not 'approved'. "
                "The worker must never promote a PENDING approval solely because a tool-execution command references it."
            )

        # Verify One-time Approval Consumption
        if approval_req.consumed_at is not None:
            return False, None, (
                f"ApprovalAlreadyConsumed: Approval '{approval_id}' was already consumed at "
                f"{approval_req.consumed_at.isoformat()} (one-time use only)."
            )

        # Verify User Authorization
        cmd_user = cmd.user_id or cmd.payload.get("user_id")
        if cmd_user and cmd_user != self._settings.jarvis_uid:
            return False, None, f"UnauthorizedUser: Requesting user '{cmd_user}' does not match authorized owner."

        # Verify Exact Tool Name
        if approval_req.tool_call.name != cmd.name:
            return False, None, (
                f"ToolMismatch: Server approval was issued for tool '{approval_req.tool_call.name}', "
                f"not '{cmd.name}'."
            )

        # Verify Original Tool Call Identifier
        expected_tool_call_id = approval_req.tool_call.id
        client_tool_call_id = cmd.payload.get("tool_call_id", expected_tool_call_id)
        if client_tool_call_id != expected_tool_call_id:
            return False, None, (
                f"ToolCallIdMismatch: Expected tool_call_id '{expected_tool_call_id}', "
                f"got '{client_tool_call_id}'."
            )

        # Verify Exact Arguments & Canonical Action Digest
        cmd_args = cmd.payload.get("arguments", {})
        computed_digest = compute_action_digest(
            agent_run_id=approval_req.agent_run_id,
            tool_call_id=expected_tool_call_id,
            tool_name=approval_req.tool_call.name,
            arguments=cmd_args,
        )

        if not approval_req.action_digest or computed_digest != approval_req.action_digest:
            return False, None, (
                "ActionDigestMismatch: Arguments were altered after approval was generated! "
                f"Expected digest '{approval_req.action_digest}', computed '{computed_digest}'."
            )

        # Consume approval atomically before execution
        try:
            self._runtime.approval_store.consume(approval_id, approval_req.tool_call, user_id=cmd_user)
        except Exception as exc:
            return False, None, f"ApprovalConsumptionFailed: {exc}"

        logger.info(
            "cloud_command_approval_verified",
            approval_id=str(approval_id),
            tool_name=cmd.name,
            action_digest=computed_digest,
        )

        # Execute verified tool with safe recovery for interruptions
        try:
            tool_res = await self._executor.execute_tool_call(
                approval_req.tool_call,
                agent_mode=agent_mode,
                is_approved=True,
            )
            return tool_res.success, tool_res.data, tool_res.error
        except Exception as exc:
            logger.error("tool_execution_interrupted", approval_id=str(approval_id), error=str(exc))
            return False, None, f"ExecutionInterrupted: Tool execution failed or was interrupted: {exc}"

    async def poll_once(self) -> CloudCommand | None:
        """Poll and execute the next available leased command."""
        worker_id = self._settings.device_id
        cmd = await self._repo.lease_next_command(
            worker_id=worker_id,
            lease_duration_seconds=self._settings.command_lease_duration_seconds,
        )

        if not cmd:
            return None

        logger.info(
            "command_leased",
            command_id=cmd.id,
            command_type=cmd.type,
            command_name=cmd.name,
            idempotency_key=cmd.idempotency_key,
        )

        # Transition LEASED -> RUNNING
        cmd.status = CommandStatus.RUNNING
        cmd = await self._repo.update(cmd)

        try:
            if self._settings.firebase_enabled and (
                cmd.user_id != self._settings.jarvis_uid
                or cmd.payload.get("user_id", cmd.user_id) != cmd.user_id
            ):
                raise PermissionError("Authenticated command owner mismatch")
            if cmd.type == "tool_execution" and cmd.name.startswith("client."):
                if self._client_requests is None:
                    raise ValueError("Phone screen service unavailable")
                data = await self._client_requests.handle(cmd.name, cmd.payload.get("arguments", {}), cmd.user_id)
                cmd.status = CommandStatus.COMPLETED
                cmd.result = {"data": data}
                cmd.error = None
            elif cmd.type == "approval_response" and cmd.payload.get("task_action_id"):
                if self._client_requests is None:
                    raise ValueError("Phone approval service unavailable")
                data = await self._client_requests.approve(cmd.payload, cmd.user_id)
                cmd.status = CommandStatus.COMPLETED
                cmd.result = {"data": data}
                cmd.error = None
            elif cmd.type == "tool_execution":
                success, data, error = await self._verify_and_execute_tool(cmd)
                if success:
                    cmd.status = CommandStatus.COMPLETED
                    cmd.result = {"data": data}
                    cmd.error = None
                else:
                    cmd.status = CommandStatus.FAILED
                    cmd.error = error
                    cmd.result = None

            elif cmd.type == "agent_run":
                user_query = cmd.payload.get("input") or cmd.payload.get("message", "")
                agent_res = await self._runtime.run(
                    user_query, source="cloud_command", user_id=cmd.user_id,
                    conversation_id=cmd.payload.get("conversation_id"),
                )

                if agent_res.state == AgentState.WAITING_APPROVAL:
                    cmd.status = CommandStatus.WAITING_APPROVAL
                    pending_tc = agent_res.pending_tool_call
                    approval_req = (
                        self._runtime.approval_store.get(agent_res.pending_approval_id)
                        if agent_res.pending_approval_id else None
                    )
                    cmd.result = {
                        "approval_id": str(agent_res.pending_approval_id) if agent_res.pending_approval_id else None,
                        "run_id": str(agent_res.id),
                        "pending_tool": pending_tc.name if pending_tc else None,
                        "arguments": pending_tc.arguments if pending_tc else {},
                        "action_digest": approval_req.action_digest if approval_req else None,
                        "expires_at": approval_req.expires_at.isoformat() if approval_req else None,
                    }
                elif agent_res.state == AgentState.COMPLETED:
                    cmd.status = CommandStatus.COMPLETED
                    cmd.result = {
                        "response": agent_res.final_response,
                        "run_id": str(agent_res.id),
                    }
                else:
                    cmd.status = CommandStatus.FAILED
                    cmd.error = agent_res.error or "Agent run did not complete successfully"

            elif cmd.type == "approval_response":
                # User response submitted from iPhone
                approval_id_str = cmd.payload.get("approval_id")
                decision = str(cmd.payload.get("decision", "")).lower()

                if not approval_id_str:
                    cmd.status = CommandStatus.FAILED
                    cmd.error = "Missing approval_id in approval_response payload"
                else:
                    try:
                        approval_id = UUID(str(approval_id_str))
                    except ValueError:
                        cmd.status = CommandStatus.FAILED
                        cmd.error = f"Invalid approval_id '{approval_id_str}'"
                        updated_cmd = await self._repo.update(cmd)
                        return updated_cmd

                    server_req = self._runtime.approval_store.get(approval_id)
                    now = datetime.now(timezone.utc)

                    if not server_req:
                        cmd.status = CommandStatus.FAILED
                        cmd.error = f"ApprovalNotFound: No approval record found for ID '{approval_id}'"
                    elif server_req.consumed_at is not None:
                        cmd.status = CommandStatus.FAILED
                        cmd.error = f"ApprovalAlreadyConsumed: Approval '{approval_id}' was already consumed at {server_req.consumed_at.isoformat()}"
                    elif server_req.status != ApprovalStatus.PENDING:
                        cmd.status = CommandStatus.FAILED
                        cmd.error = f"ApprovalInvalidState: Approval '{approval_id}' is already {server_req.status.value}"
                    elif server_req.status == ApprovalStatus.EXPIRED or now > server_req.expires_at:
                        cmd.status = CommandStatus.FAILED
                        cmd.error = f"ApprovalExpired: Approval '{approval_id}' has expired."
                    else:
                        # Verify User Authorization
                        cmd_user = cmd.user_id or cmd.payload.get("user_id")
                        if cmd_user and cmd_user != self._settings.jarvis_uid:
                            cmd.status = CommandStatus.FAILED
                            cmd.error = f"UnauthorizedUser: Requesting user '{cmd_user}' does not match authorized owner."
                        elif decision == "approved":
                            # Action digest is mandatory for approval_response
                            client_digest = cmd.payload.get("action_digest")
                            if not client_digest:
                                cmd.status = CommandStatus.FAILED
                                cmd.error = "MissingActionDigest: action_digest is mandatory for approval_response"
                            elif server_req.action_digest and client_digest != server_req.action_digest:
                                cmd.status = CommandStatus.FAILED
                                cmd.error = f"ActionDigestMismatch: Client action digest does not match server approval."
                            else:
                                completed_run = await self._runtime.resume_approval(approval_id, user_id=cmd_user)
                                if completed_run.state == AgentState.COMPLETED:
                                    cmd.status = CommandStatus.COMPLETED
                                    cmd.result = {
                                        "response": completed_run.final_response,
                                        "run_id": str(completed_run.id),
                                    }
                                elif completed_run.state == AgentState.WAITING_APPROVAL:
                                    cmd.status = CommandStatus.WAITING_APPROVAL
                                    pending_req = (
                                        self._runtime.approval_store.get(completed_run.pending_approval_id)
                                        if completed_run.pending_approval_id
                                        else None
                                    )
                                    cmd.result = {
                                        "approval_id": str(completed_run.pending_approval_id),
                                        "pending_tool": completed_run.pending_tool_call.name if completed_run.pending_tool_call else None,
                                        "arguments": completed_run.pending_tool_call.arguments if completed_run.pending_tool_call else {},
                                        "action_digest": pending_req.action_digest if pending_req else None,
                                    }
                                else:
                                    cmd.status = CommandStatus.FAILED
                                    cmd.error = completed_run.error or "Run did not complete"
                        elif decision in ("rejected", "dismissed"):
                            reason = cmd.payload.get("reason", "İşlem iPhone üzerinden reddedildi.")
                            rejected_run = await self._runtime.resume_rejection(approval_id, reason=reason, user_id=cmd_user)
                            cmd.status = CommandStatus.COMPLETED
                            cmd.result = {
                                "response": rejected_run.final_response,
                                "status": "rejected",
                            }
                        else:
                            cmd.status = CommandStatus.FAILED
                            cmd.error = f"Unsupported approval decision '{decision}'"

            elif cmd.type == "task_proposal_approval":
                cmd.status = CommandStatus.FAILED
                cmd.error = "Use a server-generated approval_id and action_digest through approval_response."

            else:
                cmd.status = CommandStatus.FAILED
                cmd.error = f"Unsupported command type '{cmd.type}'"

        except Exception as exc:
            logger.error("command_execution_exception", command_id=cmd.id, error=str(exc))
            cmd.status = CommandStatus.FAILED
            cmd.error = str(exc)

        # Persist final state
        for attempt in range(6):
            try:
                updated_cmd = await self._repo.update(cmd)
                break
            except CommandConflictError:
                # The first write may have committed before its response was lost.
                existing = await self._repo.get(cmd.id)
                if existing and existing.status == cmd.status and existing.result == cmd.result and existing.error == cmd.error:
                    updated_cmd = existing
                    break
                raise
            except Exception:
                if attempt == 5:
                    raise
                logger.warning("command_result_write_retry", command_id=cmd.id, attempt=attempt + 1)
                await asyncio.sleep(min(15, 2 ** attempt))
        logger.info(
            "command_finished",
            command_id=cmd.id,
            status=updated_cmd.status.value,
        )
        return updated_cmd

    async def _worker_loop(self) -> None:
        interval = max(1, self._settings.command_poll_interval_seconds)
        while self._running:
            try:
                cmd = await self.poll_once()
                if cmd:
                    continue
            except Exception as exc:
                logger.warning("command_worker_poll_error", error=str(exc))
            await asyncio.sleep(interval)

    async def start(self) -> None:
        """Start background polling worker."""
        if self._running:
            return
        self._running = True
        self._poll_task = asyncio.create_task(self._worker_loop())
        logger.info("command_worker_started")

    async def stop(self) -> None:
        """Gracefully stop background worker."""
        if not self._running:
            return
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        logger.info("command_worker_stopped")
