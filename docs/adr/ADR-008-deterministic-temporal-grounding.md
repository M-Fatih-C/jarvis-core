# ADR-008: Deterministic Temporal Grounding & Weekday Consistency

## Status
Accepted

## Context
During live model testing of Milestone 2's cross-session preference recall, an acceptance scenario query:
*"Yarın Jarvis projesine iki saat ayırmak istiyorum. Uygun bir zaman önerir misin?"*
anchored at `2026-09-30T21:02:05+03:00` (Wednesday) resulted in the model proposing:
`Tomorrow (Monday, 2026-10-01)`
However, mathematically `2026-10-01` is Thursday.
Relying purely on LLM token probabilities to maintain calendar and weekday arithmetic leads to subtle hallucinations where dates and weekdays conflict.

## Decision
1. **Deterministic Single Source of Truth:**
   All calendar calculations, relative date resolutions (`today`, `tomorrow`, `yesterday`, `next <weekday>`), and weekday mappings are calculated deterministically using Python's standard library `datetime` and `zoneinfo` (`core/time/temporal.py`).
2. **Context Pre-Grounding:**
   When relative temporal references are detected in user queries, `ContextBuilder` calculates the anchor date, weekday, and target dates in advance and provides explicit constraints in the system prompt:
   - `Current Anchor Date: 2026-09-30 (Wednesday / Çarşamba)`
   - `Target Date for 'tomorrow' / 'yarın': 2026-10-01 (Thursday / Perşembe)`
3. **Fail-Closed Output Sanitization:**
   As a defense-in-depth safety invariant, `AgentRuntime` filters the final verbalized text through `sanitize_response_temporal_consistency()`, automatically correcting any contradictory weekday/date pairs before presenting them to the user.

## Consequences
- **Positive:** Guarantees 100% calendar accuracy; eliminates LLM hallucinated weekdays across Turkish and English without extra inference roundtrips.
- **Positive:** Zero external dependencies (built on standard library `datetime` & `zoneinfo`).
- **Trade-off:** Minimal regex scan on final text responses.
