# Milestone 2 Architecture: Memory, Personalization & Cloud Sync

## 1. Overview
Milestone 2 expands Jarvis Core from a single-session command runner into an adaptive personal intelligence assistant with durable memory, client-side encryption, and cloud synchronization.

```
                      USER
                       │
                       ▼
                 AgentRuntime
                       │
        ┌──────────────┴──────────────┐
        │                             │
        ▼                             ▼
Memory Retrieval               Qwen3.5 Local
        │                             │
        └──────────────► Context ◄────┘
                         │
                         ▼
                      Response
                         │
                         ▼
                  Memory Extractor
                         │
                         ▼
                   Memory Policy
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   Local Store       Firestore     Mac Keychain
   (SQLite)          (Cloud Sync)  (AES Keys)
```

## 2. Memory Subsystem
- **Domain Models**: `MemoryRecord`, `MemoryCandidate`, `MemoryKind`, `MemorySensitivity`, `MemoryStatus`.
- **Extraction**: Hybrid rule-based heuristics + local Qwen JSON schema extraction.
- **Deduplication**: SHA-256 fingerprinting based on normalized canonical content and entity tuples.
- **Contradiction**: Incoming explicit user preferences supersede older contradictory records (`supersedes` pointer, old status updated to `SUPERSEDED`).
- **Security Policy**: Deterministic regex scanning for API keys, passwords, private keys, and tokens. Sensitive memories are encrypted client-side or downgraded to `SECRET_REFERENCE`.

## 3. Cloud Foundation & Command Queue
- **User Scoped Collections**:
  - `/users/{uid}/memories/{memoryId}`
  - `/users/{uid}/commands/{commandId}`
  - `/users/{uid}/devices/{deviceId}`
- **Security Rules**: Authenticated users can only read and write to `/users/{request.auth.uid}/...`.
- **Command Queue State Machine**:
  `QUEUED -> LEASED -> RUNNING -> WAITING_APPROVAL / COMPLETED / FAILED`
- **Device Telemetry**: Heartbeat loop updates `last_seen_at` and `status` periodically.
