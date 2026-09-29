"""Unit tests for QwenMLXAdapter formatting, tool call parsing, and profile settings."""

from datetime import datetime, timezone
import pytest
from core.agent.exceptions import ToolCallParseError
from core.config.settings import Settings
from core.llm.mlx_adapter import QwenMLXAdapter
from core.llm.schemas import InferenceProfile
from core.models.messages import ChatMessage, MessageRole
from core.models.tools import ToolCall


def test_format_messages_for_tokenizer_assistant_arguments_dict() -> None:
    """Requirement 2: Ensure tool arguments are formatted as a dictionary (mapping), not JSON string."""
    adapter = QwenMLXAdapter()

    tc = ToolCall(
        id="call_remind_123",
        name="reminders.create",
        arguments={
            "title": "YBS çalış",
            "due_at": "2026-09-30T19:00:00+03:00",
        },
        requested_at=datetime.now(timezone.utc),
    )

    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content="System prompt"),
        ChatMessage(role=MessageRole.USER, content="Hatırlat"),
        ChatMessage(role=MessageRole.ASSISTANT, content="", tool_calls=[tc]),
        ChatMessage(role=MessageRole.TOOL, content='{"status": "ok"}', tool_call_id=tc.id, tool_name=tc.name),
    ]

    formatted = adapter._format_messages_for_tokenizer(messages)

    # Check assistant message
    assistant_entry = formatted[2]
    assert assistant_entry["role"] == "assistant"
    assert "tool_calls" in assistant_entry

    tool_call_entry = assistant_entry["tool_calls"][0]
    assert tool_call_entry["id"] == "call_remind_123"
    assert tool_call_entry["type"] == "function"

    func_entry = tool_call_entry["function"]
    assert func_entry["name"] == "reminders.create"
    # Arguments MUST be a dict/mapping, NOT a str
    assert isinstance(func_entry["arguments"], dict)
    assert func_entry["arguments"] == {
        "title": "YBS çalış",
        "due_at": "2026-09-30T19:00:00+03:00",
    }


def test_format_messages_internal_repair_instruction() -> None:
    """Requirement 5: Ensure internal repair instructions are formatted with user role and explicit tag."""
    adapter = QwenMLXAdapter()
    messages = [
        ChatMessage(role=MessageRole.SYSTEM, content="System prompt"),
        ChatMessage(role=MessageRole.USER, content="Internal error notice", is_internal=True),
    ]

    formatted = adapter._format_messages_for_tokenizer(messages)
    assert formatted[1]["role"] == "user"
    assert "[System Internal Instruction]:" in formatted[1]["content"]


def test_parse_multiple_tool_calls_raises_error() -> None:
    """Requirement 3: Emitting multiple tool calls in a single step must raise ToolCallParseError."""
    adapter = QwenMLXAdapter()

    multiple_xml = """
<tool_call>
<function=calendar.list_events>
<parameter=limit>5</parameter>
</function>
</tool_call>
<tool_call>
<function=reminders.create>
<parameter=title>Test</parameter>
</function>
</tool_call>
"""
    with pytest.raises(ToolCallParseError, match="Jarvis V1 supports exactly one tool call per reasoning step"):
        adapter._parse_tool_calls(multiple_xml)

    multiple_json = """
<tool_call>
{"name": "calendar.list_events", "arguments": {"limit": 5}}
</tool_call>
<tool_call>
{"name": "reminders.create", "arguments": {"title": "Test"}}
</tool_call>
"""
    with pytest.raises(ToolCallParseError, match="Jarvis V1 supports exactly one tool call per reasoning step"):
        adapter._parse_tool_calls(multiple_json)


def test_parse_single_tool_call_success() -> None:
    """Single valid tool call parses cleanly."""
    adapter = QwenMLXAdapter()
    single_xml = """
<tool_call>
<function=system.get_status>
</function>
</tool_call>
"""
    clean_text, calls = adapter._parse_tool_calls(single_xml)
    assert len(calls) == 1
    assert calls[0].name == "system.get_status"


def test_inference_profile_from_settings() -> None:
    """Requirement 4: Adapter defaults to configured settings profile."""
    settings = Settings(inference_profile="deep")
    adapter = QwenMLXAdapter(settings=settings)
    assert adapter.profile == InferenceProfile.DEEP
    cfg = adapter._get_profile_config()
    assert cfg.temperature == 0.7
    assert cfg.max_tokens == 2048
