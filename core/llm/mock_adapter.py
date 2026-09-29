"""Deterministic mock LLM adapter validating real conversation semantics and temporal awareness."""

from datetime import datetime, timedelta, timezone
import re
from typing import Any
from uuid import uuid4

from core.llm.base import LLMAdapter
from core.llm.schemas import LLMResponse
from core.models.messages import ChatMessage, MessageRole
from core.models.tools import ToolCall, ToolDefinition


class MockLLMAdapter(LLMAdapter):
    """Deterministic mock LLM adapter for integration, conversation protocol, and scenario tests."""

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self._scripted_responses: list[LLMResponse] = list(responses or [])
        self._is_loaded = False
        self.call_history: list[list[ChatMessage]] = []

    def queue_response(self, response: LLMResponse) -> None:
        """Add a scripted response to the FIFO queue."""
        self._scripted_responses.append(response)

    async def load(self) -> None:
        self._is_loaded = True

    async def unload(self) -> None:
        self._is_loaded = False

    async def health(self) -> bool:
        return self._is_loaded

    def _extract_anchor_datetime(self, messages: list[ChatMessage]) -> datetime:
        """Parse current datetime from system prompt or fallback to now."""
        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                match = re.search(r"Current datetime:\s*([^\s\n]+)", msg.content)
                if match:
                    try:
                        return datetime.fromisoformat(match.group(1))
                    except Exception:
                        pass
        return datetime.now(timezone.utc)

    async def generate(self, messages: list[ChatMessage]) -> LLMResponse:
        self.call_history.append(messages)
        if self._scripted_responses:
            return self._scripted_responses.pop(0)

        last_msg = messages[-1].content if messages else ""
        return LLMResponse(
            content=f"Mock response to: {last_msg}",
            tool_calls=[],
            finish_reason="stop",
            model="mock-llm",
        )

    async def generate_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        self.call_history.append(messages)
        if self._scripted_responses:
            return self._scripted_responses.pop(0)

        # Enforce strict conversation protocol: if last message is a TOOL result,
        # there must be a preceding ASSISTANT message containing the corresponding tool_call_id!
        if messages and messages[-1].role == MessageRole.TOOL:
            tool_msg = messages[-1]
            if len(messages) < 2:
                raise ValueError("Protocol error: TOOL message cannot be the first or only message.")
            
            # Find preceding assistant message
            assistant_msg = messages[-2]
            if assistant_msg.role != MessageRole.ASSISTANT or not assistant_msg.tool_calls:
                raise ValueError(
                    f"Conversation protocol violation: TOOL result (id={tool_msg.tool_call_id}) "
                    "must directly follow an ASSISTANT message containing matching tool_calls."
                )

            matching_call = any(tc.id == tool_msg.tool_call_id for tc in assistant_msg.tool_calls)
            if not matching_call:
                raise ValueError(
                    f"Protocol violation: tool_call_id '{tool_msg.tool_call_id}' does not match "
                    f"any tool call in preceding assistant message."
                )

            # Check if this was a multi-step sequence
            # (e.g. calendar.list_events completed, now model wants to create a reminder)
            last_tool_call = assistant_msg.tool_calls[0]
            if last_tool_call.name == "calendar.list_events":
                # Check if original user prompt asked to schedule after checking calendar
                user_content = next((m.content for m in messages if m.role == MessageRole.USER), "").lower()
                if "reminder" in user_content or "hatırlat" in user_content or "ybs" in user_content:
                    anchor = self._extract_anchor_datetime(messages)
                    tomorrow_19 = (anchor + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
                    return LLMResponse(
                        content="Takviminizi kontrol ettim, yarın 19:00 uygun. Hatırlatıcı oluşturabilir miyim?",
                        tool_calls=[
                            ToolCall(
                                id=f"call_{uuid4().hex[:8]}",
                                name="reminders.create",
                                arguments={
                                    "title": "YBS çalışma",
                                    "due_at": tomorrow_19.isoformat(),
                                },
                            )
                        ],
                        finish_reason="tool_calls",
                        model="mock-llm",
                    )

            if "online" in tool_msg.content or "macOS" in tool_msg.content:
                return LLMResponse(
                    content="Sistem durumu kontrol edildi: macOS platformunda sistem online.",
                    tool_calls=[],
                    finish_reason="stop",
                    model="mock-llm",
                )
            if "Reminder created" in tool_msg.content or "created" in tool_msg.content:
                return LLMResponse(
                    content="Tamam. Yarın 19:00 için YBS çalışma hatırlatıcısı oluşturuldu.",
                    tool_calls=[],
                    finish_reason="stop",
                    model="mock-llm",
                )
            return LLMResponse(
                content=f"İşlem tamamlandı. Sonuç: {tool_msg.content}",
                tool_calls=[],
                finish_reason="stop",
                model="mock-llm",
            )

        # Inspect user prompt for tool intent
        user_prompt = ""
        for m in reversed(messages):
            if m.role == MessageRole.USER:
                user_prompt = m.content
                break

        user_prompt_lower = user_prompt.lower()
        anchor = self._extract_anchor_datetime(messages)

        # Multi-step prompt scenario
        if ("takvim" in user_prompt_lower or "calendar" in user_prompt_lower) and (
            "reminder" in user_prompt_lower or "hatırlat" in user_prompt_lower
        ):
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid4().hex[:8]}",
                        name="calendar.list_events",
                        arguments={"limit": 5},
                    )
                ],
                finish_reason="tool_calls",
                model="mock-llm",
            )

        # Scenario 1: System status
        if "durumunu kontrol et" in user_prompt_lower or "system.get_status" in user_prompt_lower:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid4().hex[:8]}",
                        name="system.get_status",
                        arguments={},
                    )
                ],
                finish_reason="tool_calls",
                model="mock-llm",
            )

        # Scenario 2: Reminder with relative date resolution
        if "hatırlat" in user_prompt_lower or "reminders.create" in user_prompt_lower:
            # Dynamically compute tomorrow 19:00 based on anchor datetime
            tomorrow_19 = (anchor + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
            return LLMResponse(
                content="Yarın 19:00 için YBS çalışma hatırlatıcısı oluşturabilirim.",
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid4().hex[:8]}",
                        name="reminders.create",
                        arguments={
                            "title": "YBS çalış",
                            "due_at": tomorrow_19.isoformat(),
                        },
                    )
                ],
                finish_reason="tool_calls",
                model="mock-llm",
            )

        # Scenario 3: Calendar list only
        if "takvim" in user_prompt_lower or "etkinlik" in user_prompt_lower:
            return LLMResponse(
                content=None,
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid4().hex[:8]}",
                        name="calendar.list_events",
                        arguments={"limit": 5},
                    )
                ],
                finish_reason="tool_calls",
                model="mock-llm",
            )

        # Default text response
        return LLMResponse(
            content="Merhaba! Ben Jarvis. Size nasıl yardımcı olabilirim?",
            tool_calls=[],
            finish_reason="stop",
            model="mock-llm",
        )
