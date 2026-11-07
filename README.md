# Jarvis Core

An experimental local-first agent core for macOS/Apple Silicon. LLM proposals
pass through a state machine, deterministic risk policy and tool executor with
approval/idempotency handling. Later modules add encrypted memory, native Apple
bridges, Gmail task extraction, cloud commands and an iOS client.

This is a separate system from `J.A.R.V.I.S`: its Python packages are `core`,
`api` and `integrations`, with different entry points, contracts and clients.
Use a separate virtual environment for each repository.

## Source map

- `core/agent/`, `policy/`, `tools/`: runtime/state, risk rules, approvals and execution.
- `core/llm/`: mock and MLX adapters; local inference requires external model weights.
- `core/memory/` and related modules: retrieval/storage and privacy controls.
- `api/`: FastAPI routes for chat, approvals and integrations.
- `integrations/`: native/cloud/service adapters, including Gmail-related workflows.
- `macos/JarvisMacAgent/`: native Swift package/IPC bridge.
- `ios/`: native iOS application and separate Swift test package.
- `cloud/`: Firebase rules/configuration and TypeScript functions.
- `docs/adr/`, `docs/architecture/`: design decisions and milestone documentation.

## Setup

Use Python 3.12+; Apple MLX inference requires supported Apple Silicon/macOS.

```bash
git clone https://github.com/M-Fatih-C/jarvis-core.git
cd jarvis-core
uv sync --locked --extra dev
cp .env.example .env
# Review .env and the supported settings in core/config/settings.py first.
uv run uvicorn api.main:app --host 127.0.0.1 --port 8765
```

The API is designed for loopback access. Mock embeddings and native integration
providers are the safe development defaults in settings; real model inference,
Apple permissions, OAuth and Firebase need their own authorized setup. Consult
the relevant ADR/setup guide rather than assuming a source module is fully configured.

## Validation

```bash
uv run pytest -q
```

The audit passed 237 existing Python tests with external network sockets blocked.
That validates the tested mocks/contracts, not model quality, live Gmail/Firebase,
native permissions or a physical iOS device. CI also defines separate Swift and
Firebase-emulator jobs; their presence is not a current passing claim. Native
tests/builds should run only in a suitable isolated platform setup.

## Policy and privacy limits

The code separates model suggestions from policy decisions and tool execution,
including sensitive-operation denial, approvals and duplicate-call handling.
These mechanisms require continuing adversarial/integration testing; they are
not a blanket guarantee against every prompt injection or malicious tool result.

Local-first does not mean every optional workflow is offline. Model/embedding
downloads, Gmail and Firebase integrations can access external services. Enable
only integrations you authorize. Preserve OAuth material, memory keys, personal
messages, calendar data and logs outside Git. Memory encryption is a component
boundary, not a claim that all process logs or device storage are encrypted.


## License and contact

No license file is included; no new license or redistribution grant is asserted. Author: [Muhammet Fatih Çetintaş](https://github.com/M-Fatih-C).
