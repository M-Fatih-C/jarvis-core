# ADR-011: Local Unix Domain Socket IPC & Security Boundaries

## Status
Accepted

## Context
Communication between the Python `AgentRuntime` and the native Swift `JarvisMacAgent` requires inter-process communication (IPC).
Exposing a local TCP port (e.g. `127.0.0.1:8766`) creates several security and isolation vulnerabilities:
1. Any process running on the host machine (including untrusted processes or browser tabs executing local requests) could scan or probe the port.
2. Port conflicts can occur if another application binds the same port.
3. TCP does not enforce file system access controls based on user UID.

## Decision
1. **Unix Domain Socket IPC:**
   Communication occurs exclusively over a local Unix Domain Socket at:
   `/tmp/jarvis-<uid>/mac-agent.sock`
2. **Strict File System Permissions:**
   - The parent directory `/tmp/jarvis-<uid>` is created with POSIX permissions `0700` (`S_IRWXU`), restricting directory traversal and file listing strictly to the current user.
   - The socket file permissions are enforced to `0600` (`S_IRUSR | S_IWUSR`).
   - Stale socket files are safely unlinked at startup.
   - World-writable IPC is strictly prohibited.
3. **Framing & Protocol:**
   - Transport: Newline-delimited JSON (NDJSON).
   - Versioning: Strict `protocol_version: 1` validation on both ends.
   - Correlation: Mandatory request `id` tracking.
   - Allowlist: Only methods registered in the Python tool layer are dispatched. Arbitrary method execution is rejected.
   - No Chain-of-Thought: Model reasoning tokens are never sent across the socket; only clean tool names and typed parameters are transmitted.
4. **Privacy Logging:**
   Event titles, notes, and calendar contents are never logged at `INFO` level.
   Only operational metadata (latency, result counts, request IDs, permission states) is emitted.

## Consequences
- **Positive:** Zero attack surface outside the current macOS user session; cannot be accessed across network or other user accounts.
- **Positive:** High throughput, low latency local transport without kernel network stack overhead.
- **Positive:** Hardened fail-closed error handling with transparent reconnection on socket disconnect.
