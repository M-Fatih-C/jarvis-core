# Jarvis V1 — Milestone 1 Architecture

## Overview

Jarvis V1 Milestone 1 establishes a local-first, autonomous AI agent core for macOS. It executes small-footprint quantized models (specifically Qwen3.5-4B MLX 4-bit) on Apple Silicon, interacts with tools through explicit type-safe definitions, and enforces a non-bypassable, deterministic Policy Engine for safety and human-in-the-loop approvals.

## Core Architectural Principles

1. **Local-First & Privacy Preserving**:
   - Model execution runs natively on Apple Silicon using MLX.
   - User context, tool execution, and decision logic remain entirely on the local device (`127.0.0.1`).
   - Logging defaults to metadata-first to eliminate sensitive content exposure.

2. **Decoupled LLM Adapter Layer**:
   - The Agent Runtime depends purely on the abstract `LLMAdapter` contract.
   - LLM providers or local backends (MLX, Mock, etc.) can be swapped without altering agent state, tool execution, or safety verification.

3. **Deterministic Policy Engine & Guardrails**:
   - The LLM *never* executes tools directly.
   - The LLM proposes actions (`ToolCall`), which are passed through the `PolicyEngine`.
   - The Policy Engine assigns decisions based on strictly defined risk levels (`R0` to `R5`) and current `AgentMode`.
   - High-risk operations (`R2`-`R4`) require explicit user approval (`WAITING_APPROVAL`), and destructive/sensitive operations (`R5`) are denied outright.

4. **Strict State Machine**:
   - Every agent run transitions across explicit states (`CREATED` -> `CONTEXT_BUILDING` -> `THINKING` -> `TOOL_PROPOSED` -> `POLICY_CHECK` -> `WAITING_APPROVAL` / `EXECUTING_TOOL` -> `PROCESSING_TOOL_RESULT` -> `RESPONDING` -> `COMPLETED` / `FAILED` / `CANCELLED`).
   - Illegal state jumps raise `AgentStateError`.

5. **Pydantic-Driven Validation & Idempotency**:
   - All tool arguments are parsed and validated via Pydantic schemas.
   - Tool calls carry unique IDs, preventing duplicate executions within a run.

## System Sequence

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant API as FastAPI Router
    participant Runtime as AgentRuntime
    participant SM as StateMachine
    participant LLM as LLMAdapter (MLX / Mock)
    participant Policy as PolicyEngine
    participant Exec as ToolExecutor
    participant Registry as ToolRegistry

    User->>API: POST /v1/chat {"message": "..."}
    API->>Runtime: run(user_input)
    Runtime->>SM: transition(CONTEXT_BUILDING)
    Runtime->>SM: transition(THINKING)
    Runtime->>LLM: generate_with_tools(messages, tools)
    LLM-->>Runtime: LLMResponse(tool_calls=[...])

    alt Tool proposed
        Runtime->>SM: transition(TOOL_PROPOSED)
        Runtime->>SM: transition(POLICY_CHECK)
        Runtime->>Policy: evaluate(agent_mode, tool_call, tool_def)
        
        alt ALLOW (R0 / R1)
            Policy-->>Runtime: PolicyDecision(ALLOW)
            Runtime->>SM: transition(EXECUTING_TOOL)
            Runtime->>Exec: execute(tool_call)
            Exec->>Registry: get(tool_call.name)
            Registry-->>Exec: JarvisTool instance
            Exec-->>Runtime: ToolResult
            Runtime->>SM: transition(PROCESSING_TOOL_RESULT)
            Runtime->>SM: transition(THINKING)
            Runtime->>LLM: generate_with_tools(...)
            LLM-->>Runtime: LLMResponse(content="Final answer")
            Runtime->>SM: transition(RESPONDING)
            Runtime->>SM: transition(COMPLETED)
            Runtime-->>API: AgentRun(state=COMPLETED)
            API-->>User: {"status": "completed", "response": "..."}

        else REQUIRE_APPROVAL (R2 / R3 / R4)
            Policy-->>Runtime: PolicyDecision(REQUIRE_APPROVAL)
            Runtime->>SM: transition(WAITING_APPROVAL)
            Runtime-->>API: AgentRun(state=WAITING_APPROVAL, approval_request=...)
            API-->>User: {"status": "waiting_approval", "approval": {...}}
            
            Note over User, API: User reviews and calls /v1/approvals/{id}/approve
            User->>API: POST /v1/approvals/{id}/approve
            API->>Runtime: resume_approval(run_id, approval_id)
            Runtime->>SM: transition(EXECUTING_TOOL)
            Runtime->>Exec: execute(tool_call)
            Exec-->>Runtime: ToolResult
            Runtime->>SM: transition(PROCESSING_TOOL_RESULT)
            Runtime->>SM: transition(THINKING)
            Runtime->>LLM: generate(...)
            Runtime->>SM: transition(RESPONDING)
            Runtime->>SM: transition(COMPLETED)
            Runtime-->>API: AgentRun(state=COMPLETED)
            API-->>User: {"status": "completed", "response": "..."}
            
        else DENY (R5 or Policy Denied)
            Policy-->>Runtime: PolicyDecision(DENY)
            Runtime->>SM: transition(PROCESSING_TOOL_RESULT)
            Runtime->>SM: transition(RESPONDING)
            Runtime->>SM: transition(COMPLETED)
        end

    else No tool proposed
        Runtime->>SM: transition(RESPONDING)
        Runtime->>SM: transition(COMPLETED)
        Runtime-->>API: AgentRun(state=COMPLETED)
        API-->>User: {"status": "completed", "response": "..."}
    end
```
