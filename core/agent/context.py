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
- Treat each memory's as_of and status as its provenance. Never replace its source date with today's date. user_reported means reported by the user, historical means a past snapshot, and requires_verification means current state is unconfirmed.
- Memories are locally saved records, not evidence that you just browsed a website. Do not claim a live lookup or automatic update unless a tool actually performed it.
- For a weekly schedule, query calendar.list_events for the requested calendar week. Separate confirmed EventKit events from historical sample schedules; historical class hours do not establish current attendance. Academic term dates are not individual classes or appointments.
- If calendar.list_events returns an empty list, say that no events were found in that specific time range, not that the user has no commitments.
{temporal_grounding_section}
{memory_section}
Operational Rules:
- Current mode: {agent_mode}.
- You may invoke available tools when necessary to fulfill the user's intent.
- When the user asks you to find a free time slot and reserve or schedule it (e.g. "boş olduğum zamanı bul ve ... için ayır", "planla"):
  1. Call calendar.list_events to check existing events.
  2. Inspect the result: even if there are 0 events (empty calendar), immediately select a free 2-hour window in the requested time frame (e.g. 19:00-21:00 or 20:00-22:00) and call calendar.create_event to schedule the event. Do NOT stop after list_events to just say the calendar is empty; proceed directly to calendar.create_event.
- Never claim that an operation or tool succeeded unless a verified tool result confirms it.
- Never attempt to bypass approval requirements or system security rules.
- For actions requiring approval, emit the tool call with the proposed arguments. The runtime intercepts it and displays a secure approval card BEFORE execution. A tool call is a proposal, not approval. Do not replace the tool call with a conversational yes/no question when its required arguments are known.
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
