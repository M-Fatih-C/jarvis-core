"""Context builder for preparing agent prompts and conversation histories."""

from core.models.agent import AgentMode
from core.models.messages import ChatMessage, MessageRole

JARVIS_SYSTEM_PROMPT_TEMPLATE = """You are Jarvis, an intelligent personal AI assistant running locally on Apple Silicon.

Operational Rules:
- Current mode: {agent_mode}.
- You may invoke available tools when necessary to fulfill the user's intent.
- Never claim that an operation or tool succeeded unless a verified tool result confirms it.
- Never attempt to bypass approval requirements or system security rules.
- If an action requires user approval, clearly state what action you intend to take and wait for confirmation.
- Never fabricate tool results, system facts, or dates.
- Keep responses helpful, concise, and accurate.
"""


class ContextBuilder:
    """Builds and manages the LLM context message chain."""

    def __init__(self, default_mode: AgentMode = AgentMode.ASSIST) -> None:
        self.default_mode = default_mode

    def build_system_message(self, agent_mode: AgentMode | None = None) -> ChatMessage:
        """Construct the standardized system message.
        
        Args:
            agent_mode: Operating mode (defaults to self.default_mode).
            
        Returns:
            ChatMessage with MessageRole.SYSTEM.
        """
        mode = agent_mode or self.default_mode
        content = JARVIS_SYSTEM_PROMPT_TEMPLATE.format(agent_mode=mode.value.upper())
        return ChatMessage(role=MessageRole.SYSTEM, content=content)

    def prepare_initial_messages(
        self,
        user_input: str,
        agent_mode: AgentMode | None = None,
    ) -> list[ChatMessage]:
        """Create the starting message list containing system prompt and user input.
        
        Args:
            user_input: Raw query from user.
            agent_mode: Operating mode.
            
        Returns:
            List containing system message and user message.
        """
        system_msg = self.build_system_message(agent_mode)
        user_msg = ChatMessage(role=MessageRole.USER, content=user_input)
        return [system_msg, user_msg]
