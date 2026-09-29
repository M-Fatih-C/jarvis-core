"""Abstract base class and contract for all Jarvis tools."""

from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, ValidationError
from core.agent.exceptions import ToolExecutionError
from core.models.tools import ToolDefinition, ToolResult


class JarvisTool(ABC):
    """Abstract base class for all tools executable within the Jarvis runtime."""

    definition: ToolDefinition
    args_schema: type[BaseModel] | None = None

    def validate_arguments(self, arguments: dict[str, Any]) -> BaseModel | dict[str, Any]:
        """Validate input arguments against tool's Pydantic schema if defined.
        
        Args:
            arguments: Raw dictionary arguments received from tool call.
            
        Returns:
            Validated Pydantic model instance or raw dict if no schema defined.
            
        Raises:
            ValidationError: If arguments do not conform to schema.
        """
        if self.args_schema is not None:
            return self.args_schema.model_validate(arguments)
        return arguments

    @abstractmethod
    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        """Execute the tool logic asynchronously.
        
        Args:
            arguments: Raw dictionary of arguments to be validated and executed.
            
        Returns:
            ToolResult containing success status, data, and/or error.
        """
        ...
