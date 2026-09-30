# Milestone 3 — Native macOS Integration, EventKit, Reminders & MacAgent

## 1. Overview & Architecture

Milestone 3 replaces mock Apple productivity tools with real, native macOS integrations for:
- **Apple Calendar** (via `EventKit`)
- **Apple Reminders** (via `EventKit`)
- **macOS Local Notifications** (via `UserNotifications`)

Python and Qwen3.5 Local **never directly invoke EventKit or PyObjC**. All Apple API interactions are performed by an independent native Swift macOS agent (`JarvisMacAgent.app`) communicating with Python via a secure Unix Domain Socket.

```text
┌────────────────────────────────────────────────────────┐
│                   Qwen3.5 Local (MLX)                  │
└───────────────────────────┬────────────────────────────┘
                            │ Proposes tool calls
                            ▼
┌────────────────────────────────────────────────────────┐
│                      AgentRuntime                      │
│     - Step loop & tool call parsing                    │
│     - Action digest calculation (SHA-256)              │
│     - Approval state machine (WAITING_APPROVAL)        │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│                      PolicyEngine                      │
│     - Evaluates risk levels (R0, R1, R2, R4)           │
│     - Enforces human approval on writes/mutations      │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│                   Python Tool Layer                    │
│     - core/tools/native_macos/                         │
│     - Argument validation & timezone checks            │
└───────────────────────────┬────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│                    MacBridgeClient                     │
│     - integrations/macos/client.py                     │
│     - Method allowlist & protocol framing              │
│     - Auto-reconnect & structured errors               │
└───────────────────────────┬────────────────────────────┘
                            │ Unix Domain Socket
                            │ /tmp/jarvis-<uid>/mac-agent.sock (0700 / 0600)
                            ▼
┌────────────────────────────────────────────────────────┐
│                JarvisMacAgent (Swift)                  │
│                                                        │
│  ┌──────────────────┐          ┌────────────────────┐  │
│  │ UnixSocketServer │          │     StatusMenu     │  │
│  │ (NDJSON framing) │          │  (Menu Bar Status) │  │
│  └────────┬─────────┘          └────────────────────┘  │
│           │                                            │
│           ▼                                            │
│  ┌──────────────────┐          ┌────────────────────┐  │
│  │  RPCDispatcher   │─────────▶│ PermissionService  │  │
│  └────────┬─────────┘          │ (TCC Authorization)│  │
│           │                    └────────────────────┘  │
│           ├───▶ CalendarService ──▶ EKEventStore       │
│           ├───▶ ReminderService ──▶ EKEventStore       │
│           └───▶ NotificationService ──▶ UNUserNotif    │
└────────────────────────────────────────────────────────┘
```

---

## 2. Unix Domain Socket IPC Protocol

- **Socket Path:** `/tmp/jarvis-<uid>/mac-agent.sock` (where `<uid>` is the current user ID, e.g. 501).
- **Directory Permissions:** `0700` (`drwx------`).
- **Socket Permissions:** `0600` (`srw-------`).
- **Framing:** Newline-delimited JSON (NDJSON).

### Request Schema
```json
{
  "protocol_version": 1,
  "id": "c1f7b03a-3bf3-4f9a-8c9e-29235d924dc1",
  "method": "calendar.list_events",
  "params": {
    "start": "2026-10-01T00:00:00+03:00",
    "end": "2026-10-01T23:59:59+03:00",
    "limit": 10
  }
}
```

### Success Response Schema
```json
{
  "protocol_version": 1,
  "id": "c1f7b03a-3bf3-4f9a-8c9e-29235d924dc1",
  "ok": true,
  "result": {
    "events": [...]
  },
  "error": null
}
```

### Error Response Schema
```json
{
  "protocol_version": 1,
  "id": "c1f7b03a-3bf3-4f9a-8c9e-29235d924dc1",
  "ok": false,
  "result": null,
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "Calendar access is denied.",
    "details": null
  }
}
```

---

## 3. Tool & Policy Risk Level Mapping

| Tool Name | Risk Level | Requires Approval | Description |
|:---|:---:|:---:|:---|
| `calendar.list_calendars` | `R0_READ` | No | List calendars and metadata |
| `calendar.list_events` | `R0_READ` | No | Bounded event query in timezone range |
| `calendar.get_event` | `R0_READ` | No | Fetch single event details by ID |
| `calendar.create_event` | `R2_WRITE` | **Yes** | Schedule new event in EventKit |
| `calendar.update_event` | `R2_WRITE` | **Yes** | Modify event (recurrence check enforced) |
| `calendar.delete_event` | `R4_DESTRUCTIVE` | **Yes** | Permanently delete calendar event |
| `reminders.list_lists` | `R0_READ` | No | List reminder lists and metadata |
| `reminders.list` | `R0_READ` | No | Filter reminders by list, due date, status |
| `reminders.create` | `R2_WRITE` | **Yes** | Create new reminder task |
| `reminders.update` | `R2_WRITE` | **Yes** | Modify existing reminder |
| `reminders.complete` | `R2_WRITE` | **Yes** | Mark reminder completed/incomplete (real mutation) |
| `reminders.delete` | `R4_DESTRUCTIVE` | **Yes** | Permanently delete reminder task |
| `notifications.status` | `R0_READ` | No | Check notification permission state |
| `notifications.show` | `R1_LOCAL_LOW` | No | Display immediate local notification banner |
| `notifications.schedule` | `R2_WRITE` | **Yes** | Schedule timed notification |
| `notifications.cancel` | `R2_WRITE` | **Yes** | Cancel scheduled notification |

---

## 4. Approval Integrity Binding (SHA-256)

To secure human-in-the-loop approvals against parameter alteration or replay attacks:
1. When an R2 or R4 tool call is proposed, `ApprovalStore.create` calculates an immutable `action_digest`:
   $$\text{digest} = \text{SHA-256}(\text{agent\_run\_id} : \text{tool\_call\_id} : \text{tool\_name} : \text{canonical\_json}(\text{arguments}))$$
2. On `resume_approval`, the digest is re-computed against the pending action. If arguments changed, `ApprovalIntegrityError` is raised and execution is denied.
3. On execution, `ApprovalStore.consume` marks `consumed_at`. Approvals are strictly one-time; any subsequent invocation raises an error.

---

## 5. Recurrence Safety Checks

Modifying or deleting recurring calendar events can inadvertently destroy an entire series.
In Milestone 3:
- If an event is recurring (`event.hasRecurrenceRules == true`), an explicit `recurrence_scope` parameter is mandatory:
  - `THIS_OCCURRENCE` (`.thisEvent`)
  - `FUTURE_OCCURRENCES` (`.futureEvents`)
- If omitted or ambiguous, the native agent refuses modification and returns `CLARIFICATION_REQUIRED`.

---

## 6. Privacy & Operational Logging

By default, user-private contents are never emitted to logs:
- `event.title`, `event.notes`, `reminder.title`, `reminder.notes` are suppressed.
- Only operational telemetry is logged:
  - `tool_name`
  - `result_count`
  - `duration_ms`
  - `request_id`
  - `permission_state`

---

## 7. Acceptance Test Execution

### Read-Only Verification (Default)
Verifies socket connection, permission states, and bounded read operations without data mutation:
```bash
python scripts/mac_integration_acceptance.py
```

### Full Write Lifecycle Verification
Creates, queries, updates, and deletes temporary tagged test records (`[JARVIS-ACCEPTANCE]`):
```bash
python scripts/mac_integration_acceptance.py --allow-write-tests --run-e2e-scenarios
```
Temporary test data is always cleaned up in a `finally` block even if later assertions fail.
