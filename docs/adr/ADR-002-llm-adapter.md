# ADR-002: Abstract LLM Adapter Interface

## Status
Accepted

## Context
Directly coupling the agent runtime, state machine, or tool execution to specific MLX functions or model-specific chat templates creates vendor lock-in and impedes unit testing, mocking, and future multi-model experimentation (e.g. remote supervisors, specialized coder models).

## Decision
All LLM interactions are mediated via the `LLMAdapter` abstract base class located in `core/llm/base.py`:
- Pure domain models (`ChatMessage`, `LLMResponse`, `ToolCall`) isolate the runtime from proprietary tokenizers or prompting formats.
- Specialized adapters (e.g. `QwenMLXAdapter`) implement model loading, memory management, chat template formatting, and tool-call parsing.
- Allows testing the entire agent loop using deterministic mock adapters without spinning up GPU inference.

## Consequences
- Requires conversion between standard schemas and model-specific tool call formats.
- Clear boundary prevents model code from touching tool execution logic or policy enforcement.
