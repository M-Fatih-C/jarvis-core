"""Abstract LLM adapter interface separating agent runtime from LLM backends."""

from abc import ABC, abstractmethod
from core.llm.schemas import LLMResponse
from core.models.messages import ChatMessage
from core.models.tools import ToolDefinition


class LLMAdapter(ABC):
    """Abstract interface defining required LLM capabilities."""

    @abstractmethod
    async def generate(
        self,
        messages: list[ChatMessage],
    ) -> LLMResponse:
        """Generate response given a list of chat messages.
        
        Args:
            messages: Conversation history.
            
        Returns:
            LLMResponse containing textual output.
        """
        ...

    @abstractmethod
    async def generate_with_tools(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """Generate response with tool definitions available to the model.
        
        Args:
            messages: Conversation history.
            tools: Registered tools available for invocation.
            
        Returns:
            LLMResponse containing text and/or proposed tool calls.
        """
        ...

    @abstractmethod
    async def health(self) -> bool:
        """Check if the adapter backend is healthy and ready for inference."""
        ...

    @abstractmethod
    async def load(self) -> None:
        """Load model weights and tokenizer into memory."""
        ...

    @abstractmethod
    async def unload(self) -> None:
        """Unload model from memory to free system resources."""
        ...
