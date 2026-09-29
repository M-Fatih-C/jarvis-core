"""Mock system inspection tools for macOS host status."""

from typing import Any
from pydantic import BaseModel
from core.models.tools import RiskLevel, ToolDefinition, ToolResult
from core.tools.base import JarvisTool


class SystemStatusInput(BaseModel):
    """Empty input schema for system.get_status."""
    pass


class SystemStatusMockTool(JarvisTool):
    """Mock tool returning current host system status."""

    definition = ToolDefinition(
        name="system.get_status",
        description="Get current operating system platform and status.",
        risk_level=RiskLevel.R0_READ,
        input_schema=SystemStatusInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = SystemStatusInput

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        self.validate_arguments(arguments)
        return ToolResult(
            tool_call_id="",
            success=True,
            data={
                "platform": "macOS",
                "status": "online",
            },
        )
