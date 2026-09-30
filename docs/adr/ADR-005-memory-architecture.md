# ADR-005: Hybrid Local-First Memory & Personalization Architecture

## Status
Accepted

## Context
Jarvis V1 requires long-term memory to learn facts, preferences, routines, and user context across separate interaction sessions. The system must operate locally on macOS with Apple Silicon while supporting cloud synchronization (Firestore) for multi-device readiness (e.g., iPhone/Siri in future milestones).

Key architectural challenges:
1. Low latency: Memory retrieval must not noticeably slow down conversational inference.
2. Privacy & Security: Sensitive preferences and secrets must not be stored in plaintext in the cloud.
3. Offline continuity: Jarvis must operate completely even when disconnected from the Internet.
4. Concurrency: Multiple device updates require conflict resolution.

## Decisions

1. **Dual Storage Strategy (Local SQLite + Cloud Firestore)**:
   - Primary operational store is local SQLite (`~/Library/Application Support/Jarvis/memory.db`).
   - Remote persistence and multi-device sharing is handled by Firestore (`/users/{uid}/memories`).
   - When offline or when cloud is disabled, local SQLite serves as the standalone authoritative store.

2. **Categorical Sensitivity Hierarchy**:
   - `NORMAL`: Plaintext local + cloud; cloud vector indexing enabled.
   - `PRIVATE`: Plaintext in local SQLite; encrypted client-side with AES-256-GCM before writing to Firestore. Cloud vector embedding is strictly forbidden.
   - `LOCAL_ONLY`: Stays solely in local SQLite; never synchronized to Firestore.
   - `SECRET_REFERENCE`: Only logical reference stored; raw credentials redacted; no vector embeddings.

3. **Hybrid Retrieval & Ranking**:
   - Vector similarity search (cosine distance on unit-normalized vectors).
   - Multi-factor scoring heuristic:
     `Score = 0.55 * Semantic + 0.20 * Importance + 0.15 * Recency + 0.10 * Confidence`
   - Context window limit enforced at `top_k = 8` and `max_memory_context_chars = 4000`.

4. **Safety & Injection Guard**:
   - Memories injected into the LLM prompt are framed as strictly passive contextual data, never executable instructions.

## Consequences
- Fast local retrieval with zero network overhead.
- Total offline operational capability.
- Cloud data leaks cannot expose private memory or secrets.
- Requires revision tracking for eventual consistency.
