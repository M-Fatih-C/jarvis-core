# ADR-004: Milestone 1.1 Core Hardening & Reliability Guarantees

## Status
Accepted

## Context
Initial testing with mock adapters revealed several core edge cases prior to connecting live local quantized models (Qwen3.5-4B MLX) and external services:
1. **Tool Message History**: The conversation history previously skipped recording the assistant's intermediate `tool_calls` message before appending the `role=tool` result, violating standard chat template protocol (`SYSTEM -> USER -> ASSISTANT(tool_calls) -> TOOL(result) -> ASSISTANT`).
2. **Approval Loop Truncation**: Resuming from `WAITING_APPROVAL` previously terminated after a single extra LLM generation, ignoring subsequent multi-step tool calls.
3. **Temporal Awareness**: Relative temporal expressions ("tomorrow at 19:00") could not be resolved reliably by the model without deterministic current datetime and timezone anchors.
4. **Tool Execution Race Conditions**: Concurrent requests with the same `tool_call_id` could bypass cache checks simultaneously.
5. **Inference Concurrency**: Concurrent requests could interleave GPU inference calls on Apple Silicon unified memory.
6. **Tool Parser Fail-Open**: Syntax errors in model-generated tool calls were caught silently instead of triggering repair retries.

## Decision
1. **Full Protocol Schema**: `ChatMessage` carries `tool_calls` on `ASSISTANT` messages and `tool_name` on `TOOL` messages.
2. **Seamless Loop Re-entry**: `resume_approval` executes the approved action and re-enters `_step_loop(run_id)`, supporting arbitrary subsequent tool reasoning.
3. **Temporal Anchoring**: System prompt deterministically injects `Current datetime: <iso>` and `User timezone: <name>` using `zoneinfo`.
4. **Single-Flight Tool Lock**: `ToolExecutor` enforces per-call mutexes via double-checked locking, guaranteeing exact-once execution under concurrency.
5. **MLX Concurrency Guard**: `QwenMLXAdapter` serializes inference with `_inference_lock`.
6. **Strict Fail-Closed Parser & Repair**: Corrupted tool call syntax raises `ToolCallParseError`, which the runtime catches to issue a formatting repair prompt up to `max_retries`.
7. **Timezone-Aware Tool Schemas**: `CreateReminderInput` and `CreateEventInput` enforce timezone awareness on all datetime fields.

## Consequences
- Guaranteed compliance with Hugging Face and Qwen chat templates.
- Completely thread-safe and race-free local execution on Apple Silicon.
- Full multi-step tool reasoning chains remain intact across human approval gates.
