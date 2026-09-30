"""Outbound polling command worker for safe transactional cloud execution."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from core.agent.runtime import AgentRuntime
from core.agent.state_machine import AgentState
from core.cloud.models import CloudCommand, CommandStatus
from core.cloud.repositories import CommandRepository, InMemoryCommandRepository
from core.config.settings import Settings, get_settings
from core.logging.setup import get_logger
from core.models.tools import ToolCall
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
    ) -> None:
        self._repo = command_repo
        self._runtime = runtime
        self._executor = tool_executor or runtime._executor
        self._settings = settings or get_settings()
        self._poll_task: asyncio.Task[None] | None = None
        self._running = False

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
            command_name=cmd.name,
            idempotency_key=cmd.idempotency_key,
        )

        # Transition LEASED -> RUNNING
        cmd.status = CommandStatus.RUNNING
        cmd = await self._repo.update(cmd)

        try:
            if cmd.type == "tool_execution":
                # Execute tool directly through Policy-aware ToolExecutor
                tool_call = ToolCall(
                    id=cmd.idempotency_key,
                    name=cmd.name,
                    arguments=cmd.payload.get("arguments", {}),
                    requested_at=datetime.now(timezone.utc),
                )
                tool_res = await self._executor.execute_tool_call(
                    tool_call,
                    agent_mode=self._runtime._settings.default_agent_mode,
                    is_approved=cmd.payload.get("is_approved", False),
                )

                if tool_res.success:
                    cmd.status = CommandStatus.COMPLETED
                    cmd.result = {"data": tool_res.data}
                    cmd.error = None
                else:
                    cmd.status = CommandStatus.FAILED
                    cmd.error = tool_res.error
                    cmd.result = None

            elif cmd.type == "agent_run":
                # Execute full cognitive loop
                user_query = cmd.payload.get("input", "")
                agent_res = await self._runtime.run(user_query, source="cloud_command")

                if agent_res.state == AgentState.WAITING_APPROVAL:
                    cmd.status = CommandStatus.WAITING_APPROVAL
                    cmd.result = {
                        "approval_id": str(agent_res.pending_approval_id),
                        "pending_tool": agent_res.pending_tool_call.name if agent_res.pending_tool_call else None,
                    }
                elif agent_res.state == AgentState.COMPLETED:
                    cmd.status = CommandStatus.COMPLETED
                    cmd.result = {"response": agent_res.final_response}
                else:
                    cmd.status = CommandStatus.FAILED
                    cmd.error = agent_res.error_message or "Agent run did not complete successfully"

            else:
                cmd.status = CommandStatus.FAILED
                cmd.error = f"Unsupported command type '{cmd.type}'"

        except Exception as exc:
            logger.error("command_execution_exception", command_id=cmd.id, error=str(exc))
            cmd.status = CommandStatus.FAILED
            cmd.error = str(exc)

        # Persist final state
        updated_cmd = await self._repo.update(cmd)
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
                    # If command was processed, immediately check if more work is queued
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
