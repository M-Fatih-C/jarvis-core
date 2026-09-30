"""Context builder for preparing agent prompts with real-time temporal awareness."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from core.config.settings import get_settings
from core.models.agent import AgentMode
from core.models.messages import ChatMessage, MessageRole
from core.time.temporal import (
    get_deterministic_temporal_grounding,
    weekday_for_date,
)

JARVIS_SYSTEM_PROMPT_TEMPLATE = """You are Jarvis, an intelligent personal AI assistant running locally on Apple Silicon.

Temporal & Contextual Awareness:
- Current datetime: {current_datetime}
- Current weekday: {current_weekday}
- User timezone: {user_timezone}
- Whenever the user specifies relative dates or times (such as "today", "tomorrow", "tonight", "next Monday", "in 2 hours"), you MUST accurately resolve the date and time against Current datetime and supply valid ISO 8601 format with timezone offset (e.g. 2026-09-30T19:00:00+03:00).
- Never hallucinate incorrect weekdays: calculate day of the week deterministically from the anchor date.
{temporal_grounding_section}
{memory_section}
Operational Rules:
- Current mode: {agent_mode}.
- You may invoke available tools when necessary to fulfill the user's intent.
- Constraint: Propose at most ONE tool call per reasoning step. Never invoke multiple tools in a single response.
- Never claim that an operation or tool succeeded unless a verified tool result confirms it.
- Never attempt to bypass approval requirements or system security rules.
- If an action requires user approval, clearly state what action you intend to take and wait for confirmation.
- Never fabricate tool results, system facts, or dates.
- Keep responses helpful, concise, and accurate.
"""


class ContextBuilder:
    """Builds and manages the LLM context message chain with temporal anchors."""

    def __init__(
        self,
        default_mode: AgentMode = AgentMode.ASSIST,
        timezone_name: str | None = None,
    ) -> None:
        self.default_mode = default_mode
        self.timezone_name = timezone_name or get_settings().default_timezone

    def get_current_datetime(self) -> datetime:
        """Return the current timezone-aware datetime in the configured user timezone."""
        try:
            tz = ZoneInfo(self.timezone_name)
        except Exception:
            tz = timezone.utc
        return datetime.now(tz=tz)

    def build_system_message(
        self,
        agent_mode: AgentMode | None = None,
        anchor_datetime: datetime | None = None,
        memory_context: str | None = None,
        temporal_grounding: str | None = None,
    ) -> ChatMessage:
        """Construct the standardized system message containing current datetime, mode, and memories.
        
        Args:
            agent_mode: Operating mode (defaults to self.default_mode).
            anchor_datetime: Optional explicit datetime for deterministic tests.
            memory_context: Optional formatted memory context string.
            temporal_grounding: Optional deterministic temporal grounding string.
            
        Returns:
            ChatMessage with MessageRole.SYSTEM.
        """
        mode = agent_mode or self.default_mode
        now_dt = anchor_datetime or self.get_current_datetime()
        mem_sec = f"\nRelevant User Memories & Preferences:\n{memory_context}\n" if memory_context else ""
        temp_sec = f"\n{temporal_grounding}\n" if temporal_grounding else ""
        current_weekday = f"{weekday_for_date(now_dt, 'en')} ({weekday_for_date(now_dt, 'tr')})"
        content = JARVIS_SYSTEM_PROMPT_TEMPLATE.format(
            current_datetime=now_dt.isoformat(),
            current_weekday=current_weekday,
            user_timezone=self.timezone_name,
            agent_mode=mode.value.upper(),
            temporal_grounding_section=temp_sec,
            memory_section=mem_sec,
        )
        return ChatMessage(role=MessageRole.SYSTEM, content=content)

    def prepare_initial_messages(
        self,
        user_input: str,
        agent_mode: AgentMode | None = None,
        anchor_datetime: datetime | None = None,
        memory_context: str | None = None,
    ) -> list[ChatMessage]:
        """Create the starting message list containing system prompt, memories, and user input.
        
        Args:
            user_input: Raw query from user.
            agent_mode: Operating mode.
            anchor_datetime: Optional explicit datetime for deterministic tests.
            memory_context: Optional formatted memory context string.
            
        Returns:
            List containing system message and user message.
        """
        now_dt = anchor_datetime or self.get_current_datetime()
        temporal_grounding = get_deterministic_temporal_grounding(
            user_input=user_input,
            anchor=now_dt,
            tz_name=self.timezone_name,
        )
        system_msg = self.build_system_message(
            agent_mode,
            anchor_datetime=now_dt,
            memory_context=memory_context,
            temporal_grounding=temporal_grounding,
        )
        user_msg = ChatMessage(role=MessageRole.USER, content=user_input)
        return [system_msg, user_msg]
