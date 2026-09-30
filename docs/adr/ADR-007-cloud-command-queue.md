# ADR-007: Outbound Cloud Command Queue & Leasing Protocol

## Status
Accepted

## Context
Future user interaction will originate from remote devices (e.g., iPhone, Siri, web companion) requesting actions to be executed on the primary Apple Silicon Mac. To prevent security vulnerabilities, the Mac must never open inbound public ports to the Internet.

## Decisions

1. **Outbound Polling Pattern**:
   - The Mac initiates strictly outbound connections to Firestore.
   - Commands are submitted by clients to `/users/{uid}/commands`.
   - The Mac `CommandWorker` polls Firestore, claims a lease, and executes the task locally.

2. **Transactional Leasing & At-Least-Once Execution**:
   - Commands support `QUEUED -> LEASED -> RUNNING -> COMPLETED / FAILED` state transitions.
   - When a worker claims a command, it sets `lease_owner = device_id` and a `lease_expires_at` timestamp.
   - If a Mac crashes mid-execution, the lease expires and another worker can safely reclaim it.

3. **Strict Idempotency**:
   - Every command specifies an `idempotency_key`.
   - Combined with ToolExecutor's per-call execution locks, duplicate transmissions never trigger duplicate real-world side effects.

4. **Security Policy Continuity**:
   - Cloud commands pass through the exact same `PolicyEngine` checks as direct terminal prompts.
   - Tools with `R2_WRITE` or `R4_DESTRUCTIVE` still require human approval before execution.
