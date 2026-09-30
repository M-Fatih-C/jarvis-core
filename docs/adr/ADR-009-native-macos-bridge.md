# ADR-009: Native Swift Bridge vs PyObjC Direct Access

## Status
Accepted

## Context
Milestone 3 requires Jarvis to integrate with native macOS productivity systems: Apple Calendar (`EventKit`), Apple Reminders (`EventKit`), and macOS Local Notifications (`UserNotifications`).
Direct access from Python via `PyObjC` was evaluated against building a decoupled native Swift macOS agent communicating via local Unix Domain Socket IPC.

Key problems with PyObjC in this architecture:
1. **Thread and RunLoop Safety:** EventKit and UserNotifications rely heavily on Grand Central Dispatch (GCD), asynchronous completion handlers, and Darwin RunLoops. Mixing Python GIL threads with PyObjC block callbacks frequently causes deadlocks or signal crashes during model inference.
2. **App Sandbox & Entitlements:** Modern macOS (macOS 14 Sonoma / macOS 15+) enforces strict TCC (Transparency, Consent, and Control) privacy access permissions (`NSCalendarsFullAccessUsageDescription`, `NSRemindersFullAccessUsageDescription`). Python command-line binaries lack a dedicated `CFBundleIdentifier` and code-signed Info.plist bundle, triggering runtime crashes or silent permission rejections.
3. **Decoupling and Independence:** The AI agent runtime (Python/MLX) and Apple native APIs operate at different lifecycles and blast radiuses.

## Decision
1. **Native Swift macOS Component (`JarvisMacAgent`):**
   Build an independent native Swift macOS application targeting macOS 14+ / Apple Silicon that manages:
   - Shared `EKEventStore`
   - Calendar queries and mutations
   - Reminder queries, creations, updates, and completions
   - Local notifications via `UNUserNotificationCenter`
   - Menu bar status item showing real-time Core connectivity and permission states
2. **Strict Architecture Boundaries:**
   - LLM decides user intent.
   - PolicyEngine decides permissions and risk tier.
   - Python Tool Layer invokes `MacBridgeClient`.
   - Native Swift MacAgent performs Apple API operations.
   - Python and Qwen never directly invoke EventKit.
3. **Provider Selection:**
   A configurable provider switch (`APPLE_INTEGRATION_PROVIDER=native_macos|mock`) enables deterministic mock execution in CI / headless environments while using real EventKit on the user's Mac.

## Consequences
- **Positive:** Zero PyObjC dependency; 100% native Swift 6 async/await performance and Apple Silicon optimization.
- **Positive:** Clean security isolation: crashes or restarts in one process do not affect the other.
- **Positive:** Full compatibility with macOS TCC privacy requirements and modern ServiceManagement APIs.
- **Trade-off:** Requires Unix Domain Socket IPC serialization between Python and Swift.
