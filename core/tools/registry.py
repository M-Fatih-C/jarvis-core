"""In-memory tool registry for discovering and managing Jarvis tools."""

from typing import List
from core.agent.exceptions import ToolNotFoundError
from core.models.tools import ToolDefinition
from core.tools.base import JarvisTool


class ToolRegistry:
    """Registry maintaining active tool instances."""

    def __init__(self) -> None:
        self._tools: dict[str, JarvisTool] = {}

    def register(self, tool: JarvisTool) -> None:
        """Register a tool instance.
        
        Args:
            tool: JarvisTool instance to register.
            
        Raises:
            ValueError: If a tool with the same name is already registered.
        """
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered.")
        self._tools[name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name.
        
        Args:
            name: The tool name to remove.
            
        Raises:
            ToolNotFoundError: If the tool does not exist.
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Cannot unregister: Tool '{name}' not found.")
        del self._tools[name]

    def get(self, name: str) -> JarvisTool:
        """Retrieve a registered tool by name.
        
        Args:
            name: The tool name.
            
        Returns:
            The JarvisTool instance.
            
        Raises:
            ToolNotFoundError: If the tool is not registered.
        """
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' not found in registry.")
        return self._tools[name]

    def list(self) -> List[JarvisTool]:
        """List all registered tool instances."""
        return list(self._tools.values())

    def list_definitions(self) -> List[ToolDefinition]:
        """List ToolDefinition schemas for all registered tools."""
        return [tool.definition for tool in self._tools.values()]

    def clear(self) -> None:
        """Clear all registered tools (useful for test isolation)."""
        self._tools.clear()
