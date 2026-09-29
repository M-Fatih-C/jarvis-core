# ADR-001: Local-First Execution on Apple Silicon with MLX

## Status
Accepted

## Context
Jarvis is envisioned as an autonomous, personal companion system integrated deeply with macOS and sensitive user resources (calendar, reminders, local files, system status). Transmitting user prompts, raw tool arguments, and context to cloud LLM providers creates privacy liabilities, high latency, operational costs, and offline vulnerabilities.

## Decision
We adopt Apple's MLX framework for on-device inference on Apple Silicon. For Milestone 1, we standardize on `mlx-community/Qwen3.5-4B-MLX-4bit`:
- High efficiency and 4-bit quantization allows full residency in unified memory.
- Sub-50ms token generation latency provides interactive responsiveness.
- Bound strictly to local loopback interface (`127.0.0.1:8765`).

## Consequences
- Requires macOS with Apple Silicon (M-series).
- Model weights (~2.5GB) must be cached locally.
- Inference compute shares GPU/Neural Engine with user workstation applications.
