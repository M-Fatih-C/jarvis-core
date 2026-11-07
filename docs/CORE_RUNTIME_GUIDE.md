# Core runtime and API guide

This guide preserves the useful setup, lifecycle and API detail from the earlier
Milestone 1 README while describing the current source. Examples are request
templates, not transcripts of a live model or external-service acceptance test.

## Development environment

From the repository root, use Python 3.12+ and the committed lockfile:

```bash
uv sync --locked --extra dev
cp .env.example .env
uv run uvicorn api.main:app --host 127.0.0.1 --port 8765
```

Review `.env` first. The ordinary API selects `QwenMLXAdapter`; mock embeddings
and mock Apple tools do **not** replace that LLM with a mock. A chat request may
load/download weights and requires a suitable Apple Silicon/MLX environment.
Development startup does not itself prove that the model is loaded. The health
response distinguishes `loaded` and `not_loaded`.

For deterministic development tests, use the fixtures and `MockLLMAdapter` in the
test suite. There is no documented environment switch that turns the normal
Uvicorn entry point into a mock LLM server.

## Request and approval workflow

Routes are defined in [`chat.py`](../api/routes/chat.py),
[`approvals.py`](../api/routes/approvals.py) and
[`health.py`](../api/routes/health.py). FastAPI also exposes local `/docs` and
`/openapi.json` when this application is running.

```bash
curl -s http://127.0.0.1:8765/health

curl -s -X POST http://127.0.0.1:8765/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Merhaba Jarvis.","agent_mode":"assist"}'
```

Chat accepts a nonempty `message` and optional `agent_mode`. Responses contain
`runId`, `status`, `message`, and nullable `approval`. When `status` is
`waiting_approval`, `approval` supplies an `id`, `tool`, and `arguments`. Read the
actual tool and arguments before approving; an LLM response is not authorization.

For example, a reminder request in `assist` mode can produce an R2 approval:

```bash
curl -s -X POST http://127.0.0.1:8765/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"Yarın saat 19:00 için çalışma hatırlatıcısı oluştur.","agent_mode":"assist"}'
```

Whether a tool is proposed depends on model output and the registered provider.
With mock Apple tools, execution is a simulated result, not a real reminder.
Use the returned approval UUID in **one** of the following requests:

```bash
approval_id='REPLACE_WITH_RETURNED_UUID'
curl -s -X POST "http://127.0.0.1:8765/v1/approvals/${approval_id}/approve"
```

```bash
approval_id='REPLACE_WITH_RETURNED_UUID'
curl -s -X POST "http://127.0.0.1:8765/v1/approvals/${approval_id}/reject" \
  -H 'Content-Type: application/json' \
  -d '{"reason":"Vazgeçtim."}'
```

Approval resumes the agent loop and can lead to another proposed tool; it does
not approve every later action in the run. The approve route returns HTTP 410
for an expired approval and HTTP 400 for other handled approval/runtime errors.
Do not retry a write by creating a new run without checking its actual outcome.

## Runtime contracts

[`state_machine.py`](../core/agent/state_machine.py) defines legal transitions:
`created -> context_building -> thinking`, then either a response or
`tool_proposed -> policy_check`. Allowed calls enter `executing_tool`; approvals
enter `waiting_approval`; results return through `processing_tool_result` to
another model step. Terminal states are `completed`, `failed` and `cancelled`.

The runtime/executor implement tool-call history, duplicate-call handling,
approval resumption, parser repair and work limits. Treat those as specific
contracts covered by tests, not proof of universal prompt-injection resistance.
See [core hardening ADR](adr/ADR-004-core-hardening.md) and the
[policy ADR](adr/ADR-003-policy-engine.md) for the original design rationale.

## Tests and model verification

```bash
uv run pytest -q
# Focused scripted scenarios with the mock LLM and mock tools:
uv run python scripts/test_model.py
```

The optional `uv run python scripts/test_model.py --mlx` selects real on-device
inference with the script's mock tool registry. It can download model weights;
it is neither a live Calendar/Gmail test nor part of an offline test claim.

The audit recorded 237 passing Python tests with external sockets blocked.
The focused script commands above are usage instructions, not newly executed
results from this documentation revision. Swift clients, Firebase-emulator jobs,
real accounts and device permissions need separate validation.

## Integration and storage boundaries

| Layer | Source | Setup/reference |
|---|---|---|
| Memory | `core/memory/` | [Architecture](architecture/milestone-2.md), [security ADR](adr/ADR-006-memory-security.md) |
| Apple bridge | `macos/JarvisMacAgent/`, `integrations/macos/` | [Permission boundary](adr/ADR-010-apple-permission-boundary.md), [IPC](adr/ADR-011-local-ipc-security.md) |
| Gmail extraction | `integrations/gmail/`, `core/email_analysis/` | [Gmail ADR](adr/ADR-012-gmail-integration-and-task-extraction.md) |
| Task planning | `core/task_planning/` | [Approval/planning ADR](adr/ADR-013-intelligent-task-approval-and-apple-planning.md) |
| Cloud commands | `cloud/`, `core/cloud/` | [Queue ADR](adr/ADR-007-cloud-command-queue.md) |
| iOS client | `ios/` | [Setup](IOS_SETUP_GUIDE.md), [client ADR](adr/ADR-014-ios-native-app-and-build-manager.md) |

Default memory/email databases live under `~/Library/Application Support/Jarvis/`.
The source also creates a conversation store in production mode. Encryption of
one memory component does not cover every database, log or cloud document.
Keep OAuth tokens, encryption keys and personal records outside version control.
Keep the API on loopback; this guide does not establish authentication for
exposing it to a LAN or the public internet.
