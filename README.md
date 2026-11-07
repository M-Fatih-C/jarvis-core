# Jarvis Core

An experimental local-first agent core for macOS/Apple Silicon. LLM proposals
pass through a state machine, deterministic risk policy and tool executor with
approval/idempotency handling. Later modules add encrypted memory, native Apple
bridges, Gmail task extraction, cloud commands and an iOS client.

This is a separate system from `J.A.R.V.I.S`: its Python packages are `core`,
`api` and `integrations`, with different entry points, contracts and clients.
Use a separate virtual environment for each repository.

## What the core does

The project separates language-model proposals from decisions about whether a
tool may execute. A run builds context, asks the LLM for a response or tool call,
checks policy, waits for approval when required, executes the approved call, and
feeds the result back into the loop. The runtime also handles parser repair,
step/tool limits, approval expiry and repeated tool-call IDs.

```text
POST /v1/chat -> context -> LLM proposal -> policy
                                           | allow -> executor
                                           | approval -> wait -> resume
                                           | deny -> report result
tool result -> next model step -> response / completion
```

Memory retrieval, Apple Calendar/Reminders, Gmail-derived tasks and remote cloud
commands extend this core. Each adds its own storage, permission or network
boundary; their presence does not establish production readiness.

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

## Configuration and policy

The main settings are loaded from environment variables or `.env` by
[`Settings`](core/config/settings.py). Important defaults are:

| Setting | Default | Purpose |
|---|---|---|
| `HOST`, `PORT` | `127.0.0.1`, `8765` | Local API address; keep the launch command on loopback |
| `MODEL_ID` | `mlx-community/Qwen3.5-4B-MLX-4bit` | External model weights for the MLX adapter |
| `INFERENCE_PROFILE` | `fast` | `fast` / `deep` inference profile |
| `MAX_AGENT_STEPS`, `MAX_TOOL_CALLS` | `12`, `8` | Runtime work limits |
| `DEFAULT_AGENT_MODE` | `assist` | Selects the policy matrix |
| `APPROVAL_TTL_SECONDS` | `300` | Pending approval lifetime |
| `DEFAULT_TIMEZONE` | `Europe/Istanbul` | Relative-date interpretation |
| `EMBEDDING_PROVIDER` | `mock` | Optional real provider: `sentence_transformers` |
| `APPLE_INTEGRATION_PROVIDER` | `mock` | Optional native provider: `native_macos` |
| `FIREBASE_ENABLED`, `GMAIL_ENABLED`, `EMAIL_SYNC_SCHEDULE_ENABLED` | `false` | Separate opt-ins for external integrations |

The [policy matrix](core/policy/rules.py) varies by mode:

| Risk | Observe | Assist (default) | Autonomous |
|---|---|---|---|
| R0 read | Allow | Allow | Allow |
| R1 local low risk | Deny | Allow | Allow |
| R2 write | Deny | Approval | Allow |
| R3 external | Deny | Approval | Approval |
| R4 destructive | Deny | Approval | Approval |
| R5 sensitive | Deny | Deny | Deny |

Risk labels come from tool definitions. Review those definitions when adding a
tool. In particular, `autonomous` changes R2 behavior; it is not an approval-only
mode. See the [runtime/API guide](docs/CORE_RUNTIME_GUIDE.md) for request examples,
approval semantics and the distinction between mock tests and real inference.

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

## Technical documentation

- [Core runtime, API examples and development workflow](docs/CORE_RUNTIME_GUIDE.md)
- [Milestone 1: runtime and policy](docs/architecture/milestone-1.md)
- [Milestone 2: memory and cloud](docs/architecture/milestone-2.md)
- [Milestone 3: native Apple integration](docs/architecture/milestone-3.md)
- [Operational setup](docs/operations/v1.md) and [iOS setup](docs/IOS_SETUP_GUIDE.md)
- [Architecture decisions](docs/adr/): model adapters, policy, memory, IPC,
  Gmail extraction, task approval and native clients

Milestone and operations documents retain development context; check them against
current settings and source before enabling services.

## License and contact

No license file is included; no new license or redistribution grant is asserted. Author: [Muhammet Fatih Çetintaş](https://github.com/M-Fatih-C).
