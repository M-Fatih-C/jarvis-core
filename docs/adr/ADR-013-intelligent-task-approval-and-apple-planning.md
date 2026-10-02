# ADR-013: Intelligent Task Approval, Apple Calendar & Reminders Planning

## Status
Accepted

## Context
In Milestone 4.1, Jarvis introduced Gmail synchronization, local AI structured email analysis via Qwen, and task extraction into `TaskProposal` records.
Milestone 4.2 bridges extracted task proposals to real macOS EventKit actions (`calendar.create_event` and `reminders.create`) while enforcing strict safety, user sovereignty, temporal accuracy, and idempotency guarantees.

The key requirements and design challenges addressed are:
1. **Human Sovereignty & PolicyEngine R2 Enforcement**:
   - Zero automatic mutations to Apple Calendar or Apple Reminders.
   - All mutations must be reviewed, customized (if desired), and explicitly approved by the user.
   - Approval is bound to the exact tool call and arguments using cryptographic action digests to prevent tampering or replay.
2. **Deterministic Lifecycle & Idempotency**:
   - Every task proposal follows 7 canonical lifecycle states: `PROPOSED`, `DISMISSED`, `APPROVED`, `WAITING_APPROVAL`, `EXECUTING`, `EXECUTED`, `FAILED`.
   - Prevent duplicate calendar events or reminders if emails are re-synced, planning is re-run, or the agent restarts.
3. **Calendar Target & iCloud Preference**:
   - Must avoid silently writing to local `On My Mac` calendars.
   - Queries writable Apple Calendars, prioritizes iCloud calendars, and warns the user if only local calendars are found.
   - Supports logical categories (`WORK`, `EDUCATION`, `PERSONAL`, `PROJECT`) within the application without generating unapproved Apple calendars.
4. **Intelligent Planning Engine**:
   - Separates deadline milestones (e.g. Exam on Oct 12) from preparation work blocks (e.g. Oct 10 19:00–21:00).
   - Calculates candidate time slots deterministically via interval arithmetic (merging existing events, adding 15-minute buffers, subtracting busy windows from allowed working hours).
   - Informs slot scoring using user memory preferences (e.g. evening study preference).
   - Rejects hallucinated deadlines: ambiguous dates require explicit user selection.
5. **Read-Back Verification**:
   - After executing an EventKit mutation over the Unix Domain Socket, Jarvis immediately reads back the record from Apple Calendar / Reminders to verify persistence before marking the proposal `EXECUTED`.
6. **Bidirectional Source Linkage & Audit**:
   - Persists the relationship between the created EventKit record, `TaskProposal`, and Gmail message ID in SQLite.
   - Enables the user to query: *"Which email created this reminder?"*.

---

## Decision

### 1. 7 Canonical Lifecycle States & Deterministic Transition Graph
- Defined `TaskProposalStatus(str, Enum)` with states:
  - `PROPOSED`
  - `DISMISSED`
  - `WAITING_APPROVAL`
  - `APPROVED`
  - `EXECUTING`
  - `EXECUTED`
  - `FAILED`
- Valid transitions are strictly governed by `transition_task_status(...)`:
  - `PROPOSED` → `WAITING_APPROVAL`, `DISMISSED`
  - `WAITING_APPROVAL` → `APPROVED`, `DISMISSED`, `PROPOSED` (re-planning)
  - `APPROVED` → `EXECUTING`, `DISMISSED`
  - `EXECUTING` → `EXECUTED`, `FAILED`
  - `FAILED` → `PROPOSED`, `WAITING_APPROVAL`
  - `DISMISSED` → `PROPOSED`
  - `EXECUTED` → Terminal (no further mutations permitted)
- Unallowed transitions raise `InvalidStateTransitionError`.

### 2. Idempotency & Composite Unique Constraints
- Implemented SQLite `task_actions` table with a composite unique index:
  ```sql
  CREATE UNIQUE INDEX idx_task_actions_idempotency
      ON task_actions(source_message_id, task_id, action_type);
  ```
- Before creating or approving an action proposal, `TaskApprovalService` checks for existing actions with the same composite key. If an action has already reached `EXECUTING` or `EXECUTED`, `DuplicateActionError` is raised, preventing double-booking or duplicate reminders.

### 3. Apple Calendar Target Manager & iCloud Preference
- Swift `JarvisMacAgent` enhances `CalendarDTO` to expose `source_title` and `source_type` from `EKSource`.
- `CalendarTargetManager` scans available calendars:
  - Filters for `allows_modifications == True`.
  - Flags iCloud calendars (`source_title == "iCloud"` or CalDAV).
  - Flags local `On My Mac` calendars (`source_title in ("On My Mac", "local", "Default")`).
  - Prioritizes iCloud calendars as default destination.
  - If no iCloud calendar exists, emits an explicit warning to prevent accidental writes to local machine-only calendars.
  - Supports logical categories (`WORK`, `EDUCATION`, `PERSONAL`, `PROJECT`) mapped into task metadata.

### 4. Intelligent Deterministic Planning Engine
- **Reminders for Deadlines**:
  - Exact or inferred deadlines (`DeadlineConfidence.EXACT` or `INFERRED`) produce Apple Reminder action proposals.
  - Uncertain deadlines (`UNCERTAIN` or `NONE`) raise `AmbiguousDeadlineError`, mandating that the user pick an explicit date.
- **Work Blocks for Tasks**:
  - Prep sessions are decoupled from deadline milestone events.
  - Reads existing events from Apple Calendar in the candidate time window via `calendar.list_events`.
  - Merges overlapping busy intervals with safety buffers (`buffer_minutes = 15`).
  - Computes free continuous segments $\ge$ requested duration (`duration_minutes = 120`).
  - Scores slots against user working/studying preferences retrieved from `MemoryService`.
  - Zero LLM hallucinations of availability: all slot boundaries are computed mathematically.

### 5. PolicyEngine R2 Approval & Action Digest Integrity
- Both `calendar.create_event` and `reminders.create` are categorized as `RiskLevel.R2_WRITE` with `requires_approval = True`.
- `PolicyEngine.evaluate(...)` mandates human approval (`REQUIRE_APPROVAL`).
- Action proposals are submitted to `ApprovalStore` where an SHA-256 action digest is computed:
  $$\text{digest} = \text{SHA256}(\text{agent\_run\_id} \parallel \text{tool\_call\_id} \parallel \text{tool\_name} \parallel \text{sorted\_arguments})$$
- Before execution, `approval_store.consume(...)` recalculates the digest. If action parameters were altered after approval, execution is immediately halted with `ApprovalIntegrityError`.
- For calendar events, a pre-execution availability re-check verifies that no conflicting event was scheduled in the target slot between approval and execution.

### 6. Read-Back Verification
- Upon dispatching the mutation to `JarvisMacAgent`:
  - For Reminders: Queries `reminders.list` to verify the reminder was committed to EventKit.
  - For Calendar: Queries `calendar.get_event` using the returned event identifier to verify existence and title/time integrity.
- If read-back fails, the action transitions to `FAILED` rather than `EXECUTED`.

### 7. Bidirectional Source Email Linkage
- SQLite `task_actions` maintains references to `task_id`, `source_message_id`, and EventKit `external_id`.
- The user or assistant can query:
  `GET /api/v1/tasks/source-email/{external_id}`
  which executes an inner join across `task_actions`, `task_proposals`, and `emails`, returning:
  - Origin email subject, sender, and received timestamp.
  - Decrypted email preview / body.
  - Associated task and action title.
- Sensitive email bodies and tokens are never copied into calendar descriptions.

---

## Consequences

- **Safety**: No automated mutation can occur on Apple Calendar or Reminders from email parsing or LLM prompt injection. All writes require human consent.
- **Reliability**: Deterministic interval math prevents double-booking. EventKit read-back guarantees synchronization state matches the OS reality.
- **User Experience**: Fast CLI (`scripts/task_cli.py`) and FastAPI endpoints enable seamless review, modification of dates/destinations, approval, and audit inquiries.
- **Backward Compatibility**: All 149 previous tests remain green; 17 new Python unit/integration tests and 1 Swift test bring total test coverage to **166 Python tests PASS** and **17 Swift tests PASS**.
