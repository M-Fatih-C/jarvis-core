# ADR-010: Apple Permission Boundary & Graceful Degradation

## Status
Accepted

## Context
Apple macOS protects Calendar and Reminders via strict TCC (Transparency, Consent, and Control) user authorization (`EKAuthorizationStatus`).
Jarvis must support:
- `notDetermined`
- `restricted`
- `denied`
- `writeOnly`
- `fullAccess`

Both Calendar and Reminders in Jarvis require full access because Jarvis reads user schedules to find free slots and writes scheduled commitments.
Attempting to prompt the user repeatedly or failing abruptly when permissions are denied degrades trust and violates privacy guidelines.

## Decision
1. **Explicit User-Driven Authorization:**
   Permission requests are NEVER triggered silently at startup.
   Permissions are prompted only as a result of an explicit user action in the `JarvisMacAgent` menu bar application or an explicit setup action.
2. **Unified Singleton EventStore:**
   `JarvisMacAgent` instantiates a single shared `EKEventStore` managed by `EventStoreService`. New event stores are never created per request.
3. **Graceful Permission Degradation:**
   If a permission is not granted (`denied`, `restricted`, or `notDetermined`):
   - Native RPC call returns structured error `{ "code": "PERMISSION_DENIED", "message": "..." }`.
   - Python `MacBridgeClient` translates this into `PermissionDeniedBridgeError`.
   - Tool returns `ToolResult(success=False, error="PERMISSION_DENIED: ...")`.
   - Qwen receives the structured error and explains the limitation clearly to the user, instructing them how to grant permissions without re-prompting loops.
4. **Action Digest Integrity Binding:**
   To secure high-risk actions against tampering during human-in-the-loop approvals, `ApprovalRequest` includes an `action_digest`:
   `SHA-256(agent_run_id : tool_call_id : tool_name : canonical_json(arguments))`
   When approved and executed, the digest is re-evaluated; if arguments were altered, execution is rejected immediately.
   Approvals are strictly one-time and expire.

## Consequences
- **Positive:** Jarvis Core remains 100% operational even when permissions are withheld.
- **Positive:** Complies with Apple Human Interface Guidelines regarding user privacy.
- **Positive:** Cryptographically binds approval decisions to immutable tool invocations.
- **Trade-off:** Calendar and Reminders tools report failure until user completes explicit one-time granting.
