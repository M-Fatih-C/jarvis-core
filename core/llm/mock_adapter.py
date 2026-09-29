"""Mock LLM adapter for deterministic unit and integration testing."""

from typing import Any
from uuid import uuid4
from core.llm.base import LLMAdapter
from core.llm.schemas import LLMResponse
from core.models.messages import ChatMessage, MessageRole
from core.models.tools import ToolCall, ToolDefinition


class MockLLMAdapter(LLMAdapter):
    """Deterministic mock LLM adapter for integration and scenario tests."""

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

        # Check if the previous message was a TOOL result
        if messages and messages[-1].role == MessageRole.TOOL:
            tool_msg = messages[-1]
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

        # Inspect user prompt for tool intent matching standard scenarios
        user_prompt = ""
        for m in reversed(messages):
            if m.role == MessageRole.USER:
                user_prompt = m.content
                break

        user_prompt_lower = user_prompt.lower()

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

        # Scenario 2: Reminder
        if "hatırlat" in user_prompt_lower or "reminders.create" in user_prompt_lower:
            return LLMResponse(
                content="Yarın 19:00 için YBS çalışma hatırlatıcısı oluşturabilirim.",
                tool_calls=[
                    ToolCall(
                        id=f"call_{uuid4().hex[:8]}",
                        name="reminders.create",
                        arguments={
                            "title": "YBS çalış",
                            "due_at": "2026-09-30T19:00:00+03:00",
                        },
                    )
                ],
                finish_reason="tool_calls",
                model="mock-llm",
            )

        # Scenario 3: Calendar list
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
