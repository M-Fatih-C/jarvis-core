# Milestone 2 & 2.1 Architecture: Memory, Personalization & Production Verification

## 1. Overview
Milestone 2 expands Jarvis Core from a single-session command runner into an adaptive personal intelligence assistant with durable memory, client-side encryption, and cloud synchronization. Milestone 2.1 hardens and verifies all production execution paths.

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

## 4. Deterministic Temporal Grounding (ADR-008)
- Eliminates LLM hallucination of weekdays and calendar mathematics.
- Single source of truth via Python `datetime` and `zoneinfo` (`core/time/temporal.py`).
- Automatic pre-grounding in system prompts and fail-closed response sanitization.

## 5. Production-Path Verification Matrix

Every execution path in Jarvis Core is explicitly classified and verified through dedicated suites:

| Execution Path | Verification Level | Verification Mechanism | Status |
| :--- | :--- | :--- | :---: |
| **Domain Logic & Invariants** | Unit Verification | Pytest (82 tests across memory, tools, policy, cloud, time) | **VERIFIED** |
| **Agent State Machine & Protocols** | Mock Integration Verification | `tests/integration/test_milestone_2_acceptance.py` | **VERIFIED** |
| **Process-Restart Persistence** | Two-Process Persistence Verification | `tests/integration/test_process_persistence.py` (Subprocess A store -> Subprocess B retrieve) | **VERIFIED** |
| **Firestore Cloud Integration** | Firestore Emulator Strict Acceptance | `scripts/firebase_emulator_test.py --require-emulator` in GitHub CI (`firebase-emulator` job) | **VERIFIED** |
| **LLM Reasoning & Multi-step Loop** | Real Apple Silicon MLX Verification | `scripts/test_model.py --mlx` with `mlx-community/Qwen3.5-4B-MLX-4bit` | **VERIFIED** |
| **Semantic Vector Retrieval** | Real Embedding Model Verification | `scripts/memory_demo.py --mlx --real-embeddings` with `intfloat/multilingual-e5-small` | **VERIFIED** |
| **Key Management & Cryptography** | Real macOS Keychain Verification | `scripts/keychain_acceptance.py` with native Apple Keychain Services (`com.jarvis.acceptance_test`) | **VERIFIED** |
| **Temporal Grounding & Weekdays** | Deterministic Temporal Verification | `tests/unit/test_temporal.py` and scheduling acceptance assertions | **VERIFIED** |
