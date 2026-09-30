import Foundation
import UserNotifications
import AppKit

public protocol NotificationServiceProtocol: Sendable {
    func getStatus() async -> [String: Sendable]
    func showNotification(title: String, body: String, identifier: String?) async throws -> [String: Sendable]
    func scheduleNotification(title: String, body: String, delaySeconds: Double?, scheduledAt: Date?, identifier: String?) async throws -> [String: Sendable]
    func cancelNotification(identifier: String) async -> Bool
}

public enum NotificationServiceError: Error, LocalizedError {
    case permissionDenied(String)
    case schedulingFailed(String)

    public var errorDescription: String? {
        switch self {
        case .permissionDenied(let msg): return "Notifications permission denied: \(msg)"
        case .schedulingFailed(let msg): return "Notification schedule failed: \(msg)"
        }
    }
}

public final class NotificationService: NotificationServiceProtocol, @unchecked Sendable {
    private var center: UNUserNotificationCenter? {
        if Bundle.main.bundleIdentifier != nil {
            return UNUserNotificationCenter.current()
        }
        return nil
    }

    public init() {}

    public func getStatus() async -> [String: Sendable] {
        guard let center = self.center else {
            return [
                "status": "authorized",
                "notifications_enabled": true,
            ]
        }

        let settings = await center.notificationSettings()
        let statusStr: String
        switch settings.authorizationStatus {
        case .authorized, .provisional, .ephemeral: statusStr = "authorized"
        case .denied: statusStr = "denied"
        case .notDetermined: statusStr = "not_determined"
        @unknown default: statusStr = "unknown"
        }

        return [
            "status": statusStr,
            "notifications_enabled": (settings.authorizationStatus == .authorized)
        ]
    }

    public func showNotification(title: String, body: String, identifier: String?) async throws -> [String: Sendable] {
        let id = identifier ?? "jarvis-\(UUID().uuidString)"

        if let center = self.center {
            let settings = await center.notificationSettings()
            guard settings.authorizationStatus == .authorized else {
                throw NotificationServiceError.permissionDenied("Notification access has not been granted by user.")
            }

            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default

            let request = UNNotificationRequest(identifier: id, content: content, trigger: nil)
            do {
                try await center.add(request)
                return [
                    "delivered": true,
                    "identifier": id,
                    "title": title,
                    "body": body,
                ]
            } catch {
                throw NotificationServiceError.schedulingFailed(error.localizedDescription)
            }
        } else {
            // Script fallback when running as a standalone binary outside an .app bundle
            let escapedBody = body.replacingOccurrences(of: "\"", with: "\\\"")
            let escapedTitle = title.replacingOccurrences(of: "\"", with: "\\\"")
            let script = "display notification \"\(escapedBody)\" with title \"\(escapedTitle)\""
            let appleScript = NSAppleScript(source: script)
            var errorInfo: NSDictionary?
            appleScript?.executeAndReturnError(&errorInfo)
            return [
                "delivered": true,
                "identifier": id,
                "title": title,
                "body": body,
            ]
        }
    }

    public func scheduleNotification(
        title: String,
        body: String,
        delaySeconds: Double?,
        scheduledAt: Date?,
        identifier: String?
    ) async throws -> [String: Sendable] {
        let id = identifier ?? "jarvis-\(UUID().uuidString)"

        if let center = self.center {
            let settings = await center.notificationSettings()
            guard settings.authorizationStatus == .authorized else {
                throw NotificationServiceError.permissionDenied("Notification access has not been granted by user.")
            }

            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default

            let trigger: UNNotificationTrigger
            if let delay = delaySeconds, delay > 0 {
                trigger = UNTimeIntervalNotificationTrigger(timeInterval: delay, repeats: false)
            } else if let scheduled = scheduledAt {
                let delay = scheduled.timeIntervalSince(Date())
                let safeDelay = max(1.0, delay)
                trigger = UNTimeIntervalNotificationTrigger(timeInterval: safeDelay, repeats: false)
            } else {
                trigger = UNTimeIntervalNotificationTrigger(timeInterval: 1.0, repeats: false)
            }

            let request = UNNotificationRequest(identifier: id, content: content, trigger: trigger)
            do {
                try await center.add(request)
                return [
                    "scheduled": true,
                    "identifier": id,
                    "title": title,
                    "body": body,
                ]
            } catch {
                throw NotificationServiceError.schedulingFailed(error.localizedDescription)
            }
        } else {
            // Fallback for standalone binary
            return try await showNotification(title: title, body: body, identifier: id)
        }
    }

    public func cancelNotification(identifier: String) async -> Bool {
        center?.removePendingNotificationRequests(withIdentifiers: [identifier])
        center?.removeDeliveredNotifications(withIdentifiers: [identifier])
        return true
    }
}
