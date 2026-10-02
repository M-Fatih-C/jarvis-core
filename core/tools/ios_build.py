"""Native iOS Build Manager tools exposed to the Jarvis Agent and Policy Engine."""

from typing import Any
from pydantic import BaseModel, Field
from core.build_manager.manager import BuildManager
from core.logging.setup import get_logger
from core.models.tools import RiskLevel, ToolDefinition, ToolResult
from core.tools.base import JarvisTool

logger = get_logger("jarvis.tools.ios_build")


# --- Schemas ---

class EmptyInput(BaseModel):
    """Empty input schema for parameterless build tools."""
    pass


class BuildInput(BaseModel):
    """Input parameters for ios.build.build."""
    clean: bool = Field(default=False, description="Whether to perform a clean build prior to compilation")
    allow_provisioning_updates: bool = Field(default=True, description="Whether to allow Xcode to request provisioning updates")


class AutoRenewInput(BaseModel):
    """Input parameters for setting auto-renewal state."""
    enabled: bool = Field(description="Enable or disable automated wireless build and renewal")


# --- Tools ---

class IOSBuildStatusTool(JarvisTool):
    """Tool to inspect iOS build manager status, device connectivity, and provisioning profile expiration."""

    definition = ToolDefinition(
        name="ios.build.status",
        description="Get current status of the iOS build system, device connectivity, and provisioning profile expiration.",
        risk_level=RiskLevel.R0_READ,
        input_schema=EmptyInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = EmptyInput

    def __init__(self, manager: BuildManager | None = None) -> None:
        self.manager = manager or BuildManager()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            status = await self.manager.get_status()
            return ToolResult(tool_call_id="", success=True, data=status)
        except Exception as exc:
            logger.error("ios_build_status_failed", error=str(exc))
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class IOSBuildCheckDeviceTool(JarvisTool):
    """Tool to verify connectivity, pairing status, and developer mode on the designated iPhone."""

    definition = ToolDefinition(
        name="ios.build.check_device",
        description="Check connectivity, pairing status, and developer mode on the designated iPhone.",
        risk_level=RiskLevel.R0_READ,
        input_schema=EmptyInput.model_json_schema(),
        requires_approval=False,
    )
    args_schema = EmptyInput

    def __init__(self, manager: BuildManager | None = None) -> None:
        self.manager = manager or BuildManager()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            dev = await self.manager.check_device()
            if dev:
                return ToolResult(tool_call_id="", success=True, data=dev.model_dump(mode="json"))
            return ToolResult(
                tool_call_id="",
                success=False,
                error=f"Designated iPhone '{self.manager.target_device_name}' is not reachable via USB or Wi-Fi."
            )
        except Exception as exc:
            logger.error("ios_build_check_device_failed", error=str(exc))
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class IOSBuildBuildTool(JarvisTool):
    """Tool to compile and sign the native Jarvis iOS app (R2 Write, requires human approval)."""

    definition = ToolDefinition(
        name="ios.build.build",
        description="Compile and code-sign the native Jarvis iOS application for the target iPhone.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=BuildInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = BuildInput

    def __init__(self, manager: BuildManager | None = None) -> None:
        self.manager = manager or BuildManager()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        validated = self.validate_arguments(arguments)
        clean = getattr(validated, "clean", False)
        allow_updates = getattr(validated, "allow_provisioning_updates", True)

        try:
            res = await self.manager.build(
                allow_provisioning_updates=allow_updates,
                clean_first=clean
            )
            return ToolResult(
                tool_call_id="",
                success=res.success,
                data={
                    "success": res.success,
                    "artifact_path": res.artifact.app_path if res.artifact else None,
                    "duration_seconds": res.duration_seconds,
                    "codesign_valid": res.artifact.codesign_valid if res.artifact else False,
                    "user_action_required": res.requires_user_action,
                },
                error=res.error
            )
        except Exception as exc:
            logger.error("ios_build_build_failed", error=str(exc))
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class IOSBuildInstallTool(JarvisTool):
    """Tool to wirelessly or wired install the compiled Jarvis iOS app (R2 Write, requires human approval)."""

    definition = ToolDefinition(
        name="ios.build.install",
        description="Deploy and install the compiled Jarvis iOS application onto the target iPhone via devicectl.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=EmptyInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = EmptyInput

    def __init__(self, manager: BuildManager | None = None) -> None:
        self.manager = manager or BuildManager()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            res = await self.manager.install()
            return ToolResult(
                tool_call_id="",
                success=res.success,
                data={
                    "success": res.success,
                    "device_id": res.device_id,
                    "bundle_id": res.bundle_id,
                    "duration_seconds": res.duration_seconds,
                    "device_locked": res.device_locked,
                },
                error=res.error
            )
        except Exception as exc:
            logger.error("ios_build_install_failed", error=str(exc))
            return ToolResult(tool_call_id="", success=False, error=str(exc))


class IOSBuildRenewTool(JarvisTool):
    """Tool to renew the provisioning profile and re-sign the app (R2 Write, requires human approval)."""

    definition = ToolDefinition(
        name="ios.build.renew",
        description="Trigger automatic provisioning profile renewal and re-signing for the target iPhone.",
        risk_level=RiskLevel.R2_WRITE,
        input_schema=EmptyInput.model_json_schema(),
        requires_approval=True,
    )
    args_schema = EmptyInput

    def __init__(self, manager: BuildManager | None = None) -> None:
        self.manager = manager or BuildManager()

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        try:
            res = await self.manager.renew()
            return ToolResult(
                tool_call_id="",
                success=res.get("success", False),
                data=res,
                error=res.get("error")
            )
        except Exception as exc:
            logger.error("ios_build_renew_failed", error=str(exc))
            return ToolResult(tool_call_id="", success=False, error=str(exc))
