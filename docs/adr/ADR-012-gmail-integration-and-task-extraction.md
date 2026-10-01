# ADR-012: Gmail Integration, Intelligent Email Analysis & Task Extraction

## Status
Accepted

## Context
Jarvis requires secure access to Gmail to synchronize incoming messages, classify their importance, extract actionable tasks with deadlines, and dispatch native macOS notifications.
Emails contain sensitive personal and professional data as well as untrusted external content.
The system must guarantee:
1. Privacy-first, local AI processing without sending email bodies to external third-party cloud LLMs.
2. Read-only access (`gmail.readonly`) without any mailbox mutation capabilities (no sending, deleting, or archiving).
3. Secure token management in macOS Keychain without plaintext secrets in files, logs, or git.
4. Resistance against prompt injection embedded within email bodies.
5. Strict milestone separation: task proposals are extracted and stored, but never automatically executed on Apple Calendar or Reminders without explicit human approval.

## Decision

### 1. OAuth 2.0 PKCE for Installed Desktop Apps
- Implemented RFC 7636 PKCE (S256 code challenge) and RFC 8252 loopback authorization flow.
- Minimal scope: `https://www.googleapis.com/auth/gmail.readonly`.
- Refresh and access tokens are encrypted and stored in native macOS Keychain via `keyring` (`service="com.jarvis.gmail"`).
- Automatic token refresh is performed on token expiry without exposing credentials in logs.
- Disconnection revokes tokens remotely via Google OAuth revocation endpoint and clears Keychain.

### 2. Message Normalization & Persistence
- Multipart MIME traversal extracts clean plain text, converts HTML to readable plain text (stripping scripts, styles, and tags while preserving link labels and URLs), and extracts attachment metadata without downloading payload files.
- Deduplication is guaranteed using stable Gmail message IDs and SHA-256 content hashes.
- Local SQLite database (`~/Library/Application Support/Jarvis/email.db`) stores sync cursors, emails, analysis results, task proposals, and dispatched notifications.

### 3. Local AI Analysis & Anti-Prompt-Injection Framing
- On-device Qwen3.5-4B MLX adapter executes structured email analysis.
- Prompt injection defense: Email text is framed as passive untrusted data inside `<EMAIL_CONTENT>` tags with explicit delimiter escaping and system prompt isolation. Email text can never trigger tool calls or override agent directives.
- Structured Pydantic validation enforces:
  - 7 categories: `work_career`, `education`, `finance`, `meetings`, `personal`, `general`, `promotional`.
  - 3 importance tiers: `HIGH`, `MEDIUM`, `LOW`.
  - Temporal grounding using user timezone (`default_timezone`). Ambiguous deadlines are marked `uncertain` with original text preserved.

### 4. Milestone 4.1 Safety Boundary (Zero Automatic Mutations)
- Actionable emails produce structured `TaskProposal` instances (`status="proposed"`).
- Milestone 4.1 strictly prohibits automatic modifications to Apple Calendar or Apple Reminders.
- Any future execution of proposed tasks must pass through Policy Engine **R2** `WAITING_APPROVAL` human-in-the-loop gating.

### 5. Smart Notifications & Content Sanitization
- Evaluates importance criteria (urgent response, approaching exam/registration deadlines, security alerts, high-importance career updates).
- Promotional and low-priority messages are strictly silenced.
- OTPs, passwords, and sensitive URLs are masked before banner dispatch.
- Sent notification IDs (`gmail_<message_id>`) prevent duplicate user alerts.

### 6. Configurable Scheduling & Opt-in
- Background synchronization is strictly opt-in (`email_sync_schedule_enabled: false` by default).
- When enabled, runs at configured times (default 09:00 and 20:00 local time).
- Detects missed sync slots when Mac wakes from sleep and performs safe catch-up.

---

## Google Cloud OAuth 2.0 Setup Instructions

To connect Jarvis to a live Gmail account:

1. **Google Cloud Console:**
   - Create or select a Google Cloud project at [console.cloud.google.com](https://console.cloud.google.com).
   - Navigate to **APIs & Services** → **Library** and enable **Gmail API**.
2. **OAuth Consent Screen:**
   - Configure **External** user type.
   - Add scope: `https://www.googleapis.com/auth/gmail.readonly`.
   - Add your Google account under **Test users**.
3. **Credentials:**
   - Navigate to **Credentials** → **Create Credentials** → **OAuth client ID**.
   - Select Application type: **Desktop app**.
   - Name: `Jarvis Desktop Agent`.
   - Copy the generated `Client ID` and `Client Secret`.
4. **Environment Configuration:**
   Add to your local `.env` file (or export in environment):
   ```bash
   GMAIL_ENABLED=true
   GMAIL_CLIENT_ID="<your-client-id>.apps.googleusercontent.com"
   GMAIL_CLIENT_SECRET="<your-client-secret>"
   ```

---

## Consequences
- **Positive:** Complete privacy preservation; email bodies never leave the user's Mac.
- **Positive:** High resilience against malformed inputs, transient network outages, and rate limits.
- **Positive:** Strict separation between extracted task proposals and actual external mutations.
