# ADR-014: Native SwiftUI iPhone App & Wireless Build Manager

## Status
Accepted

## Context
JARVIS V1 Milestone 5 establishes a native iPhone interface and automated deployment infrastructure. 
The client hardware is an iPhone 13 running iOS 17+, paired with the local Mac mini M4 server hosting Qwen local models, Firestore Command Queues, and the native Mac agent.
The user develops on a free Apple Developer Personal Team (`T92VJM9C5B`), which limits provisioning profile lifespans to 7 days and lacks APNs background push privileges.

## Decision

### 1. Client Architecture (Phase A)
- **Framework**: Native SwiftUI with MVVM architecture.
- **Offloaded Intelligence**: No LLM models run directly on the iPhone. All heavy reasoning and natural language processing occur on the local Mac mini.
- **Command Queue & Data Flow**:
  `iPhone SwiftUI → Firebase Authentication → Firestore Command Queue → Mac Worker → AgentRuntime → Qwen → PolicyEngine → Tool execution → Firestore result → iPhone UI`
- **6 Core Screens**:
  1. `ChatView`: Natural language chat with Jarvis.
  2. `ApprovalsView`: Real-time task proposal reviews, diff inspection, and exact execution approvals.
  3. `CalendarScheduleView`: Dual-view schedule showing unified iCloud Calendar events and Task Proposal blocks.
  4. `EmailsView`: Analyzed Gmail messages, actionable proposal linkages, and importance indicators.
  5. `DeviceStatusView`: Mac mini connectivity, model inference latency, and local bridge health.
  6. `SettingsView`: User authentication, active session management, auto-renew toggle, and server endpoint configuration.
- **Tamper Proofing**: Reuses the cryptographic action digest model from Milestone 4.2; modifying any proposal parameter invalidates pending approvals.

### 2. iOS Build & Wireless Deployment Manager (Phase B)
- **Package**: `core/build_manager/`
  - `device_monitor.py`: Discovers devices using official `xcrun devicectl list devices --json-output`, parsing JSON to distinguish wired and wireless transport.
  - `signing.py`: Uses `security cms -D -i embedded.mobileprovision` to extract `ExpirationDate` and compute a ~2-day renewal window. Detects when Apple ID login or 2FA is required.
  - `builder.py`: Orchestrates `xcodebuild` targeting `JarvisiOS.xcodeproj`, using Personal Team automatic signing with `-allowProvisioningUpdates`.
  - `installer.py`: Executes wireless/wired installation via `xcrun devicectl device install app --device <DEVICE_ID> <APP_PATH>`. Preserves app container and user data (no clean uninstall).
  - `notifier.py`: Emits native macOS notifications for all lifecycle events (`BUILD_SUCCEEDED`, `INSTALL_SUCCEEDED`, `DEVICE_UNAVAILABLE`, `SIGNING_EXPIRED`, `USER_ACTION_REQUIRED`, `BUILD_FAILED`, `INSTALL_FAILED`).
  - `manager.py`: Coordinator enforcing mutex locks (`asyncio.Lock`) preventing concurrent builds, rate limits, and 15-minute launchd background checks.

### 3. Safety & Tool Governance
- Only 5 whitelisted tools are exposed:
  - `ios.build.status` (R0_READ, no approval)
  - `ios.build.check_device` (R0_READ, no approval)
  - `ios.build.build` (R2_WRITE, requires approval)
  - `ios.build.install` (R2_WRITE, requires approval)
  - `ios.build.renew` (R2_WRITE, requires approval)
- No arbitrary shell commands or untrusted repository downloads are allowed.
- Auto-deploy condition:
  `device_reachable AND user_opted_in AND (provisioning_expiration_approaching OR approved_new_build_available OR explicit_manual_build_requested)`

## Consequences
- **Positive**: Seamless native iOS mobile experience without draining iPhone battery or thermal throttling. Automated wireless maintenance overcomes the 7-day Personal Team provisioning limit.
- **Constraints**: Free Personal Teams require periodic Apple ID authentication via Xcode GUI if sessions expire; background push relies on iCloud synchronization rather than APNs.
