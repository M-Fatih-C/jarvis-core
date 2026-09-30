import Foundation
import EventKit
import UserNotifications

public enum PermissionStatus: String, Codable, Sendable {
    case notDetermined = "not_determined"
    case restricted = "restricted"
    case denied = "denied"
    case fullAccess = "full_access"
    case writeOnly = "write_only"
    case authorized = "authorized"
    case unknown = "unknown"
}

public protocol PermissionServiceProtocol: Sendable {
    func calendarStatus() -> PermissionStatus
    func remindersStatus() -> PermissionStatus
    func notificationStatus() async -> PermissionStatus
    func requestCalendarAccess() async -> Bool
    func requestRemindersAccess() async -> Bool
    func requestNotificationAccess() async -> Bool
}

public final class PermissionService: PermissionServiceProtocol, @unchecked Sendable {
    private let eventStore: EKEventStore

    public init(eventStore: EKEventStore) {
        self.eventStore = eventStore
    }

    public func calendarStatus() -> PermissionStatus {
        let status = EKEventStore.authorizationStatus(for: .event)
        switch status {
        case .notDetermined: return .notDetermined
        case .restricted: return .restricted
        case .denied: return .denied
        case .fullAccess: return .fullAccess
        case .writeOnly: return .writeOnly
        @unknown default: return .unknown
        }
    }

    public func remindersStatus() -> PermissionStatus {
        let status = EKEventStore.authorizationStatus(for: .reminder)
        switch status {
        case .notDetermined: return .notDetermined
        case .restricted: return .restricted
        case .denied: return .denied
        case .fullAccess: return .fullAccess
        case .writeOnly: return .writeOnly
        @unknown default: return .unknown
        }
    }

    public func notificationStatus() async -> PermissionStatus {
        guard Bundle.main.bundleIdentifier != nil else {
            return .authorized
        }
        let settings = await UNUserNotificationCenter.current().notificationSettings()
        switch settings.authorizationStatus {
        case .notDetermined: return .notDetermined
        case .denied: return .denied
        case .authorized, .provisional, .ephemeral: return .authorized
        @unknown default: return .unknown
        }
    }

    public func requestCalendarAccess() async -> Bool {
        do {
            return try await eventStore.requestFullAccessToEvents()
        } catch {
            return false
        }
    }

    public func requestRemindersAccess() async -> Bool {
        do {
            return try await eventStore.requestFullAccessToReminders()
        } catch {
            return false
        }
    }

    public func requestNotificationAccess() async -> Bool {
        guard Bundle.main.bundleIdentifier != nil else {
            return true
        }
        do {
            return try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
        } catch {
            return false
        }
    }
}
