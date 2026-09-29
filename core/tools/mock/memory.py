"""Mock long-term memory retrieval tool."""

from typing import Any
from pydantic import BaseModel, Field
from core.models.tools import RiskLevel, ToolDefinition, ToolResult
from core.tools.base import JarvisTool


class MemorySearchInput(BaseModel):
    """Input parameters for memory.search."""
    query: str = Field(description="Search query to match relevant memory records")


class MemorySearchMockTool(JarvisTool):
    """Mock tool simulating user memory retrieval."""

    definition = ToolDefinition(
        name="memory.search",
        description="Search past memories, preferences, and user facts.",
        risk_level=RiskLevel.R0_READ,
        input_schema=MemorySearchInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = MemorySearchInput

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.validate_arguments(arguments)
        return ToolResult(
            tool_call_id="",
            success=True,
            data={
                "memories": [
                    "User prefers evening study sessions."
                ]
            },
        )
