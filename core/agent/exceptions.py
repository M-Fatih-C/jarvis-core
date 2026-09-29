"""Custom exceptions for Jarvis agent execution and subsystems."""


class JarvisError(Exception):
    """Base exception for all Jarvis errors."""
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class LLMError(JarvisError):
    """Raised when LLM loading, generation, or parsing fails."""
    pass


class ToolNotFoundError(JarvisError):
    """Raised when a requested tool does not exist in the registry."""
    pass


class ToolExecutionError(JarvisError):
    """Raised when tool execution fails unexpectedly."""
    pass


class PolicyDeniedError(JarvisError):
    """Raised when a tool call is explicitly denied by the policy engine."""
    pass


class ApprovalRequiredError(JarvisError):
    """Raised when an operation cannot proceed without human approval."""
    pass


class ApprovalExpiredError(JarvisError):
    """Raised when attempting to approve an expired request."""
    pass


class AgentStateError(JarvisError):
    """Raised on invalid state machine transitions."""
    pass


class AgentStepLimitError(JarvisError):
    """Raised when the agent exceeds maximum allowed reasoning steps or tool calls."""
    pass
