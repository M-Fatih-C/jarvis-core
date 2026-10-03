import Foundation
import ServiceManagement

public final class RPCDispatcher: Sendable {
    private let calendarService: CalendarServiceProtocol
    private let reminderService: ReminderServiceProtocol
    private let notificationService: NotificationServiceProtocol
    private let permissionService: PermissionServiceProtocol

    public init(
        calendarService: CalendarServiceProtocol,
        reminderService: ReminderServiceProtocol,
        notificationService: NotificationServiceProtocol,
        permissionService: PermissionServiceProtocol
    ) {
        self.calendarService = calendarService
        self.reminderService = reminderService
        self.notificationService = notificationService
        self.permissionService = permissionService
    }

    public func dispatch(request: RPCRequest) async -> RPCResponse {
        do {
            switch request.method {
            case "system.health":
                let calPerm = permissionService.calendarStatus().rawValue
                let remPerm = permissionService.remindersStatus().rawValue
                let notifPerm = await permissionService.notificationStatus().rawValue
                let healthResult: [String: Sendable] = [
                    "agent": "ready",
                    "protocol_version": 1,
                    "calendar_permission": calPerm,
                    "reminders_permission": remPerm,
                    "notification_permission": notifPerm,
                ]
                return .success(id: request.id, result: AnyCodable(healthResult))

            case "system.request_permissions":
                let reqCal = (request.params["calendar"]?.value as? Bool) ?? true
                let reqRem = (request.params["reminders"]?.value as? Bool) ?? true
                let reqNotif = (request.params["notifications"]?.value as? Bool) ?? true

                var calGranted = false
                var remGranted = false
                var notifGranted = false

                if reqCal {
                    calGranted = await permissionService.requestCalendarAccess()
                }
                if reqRem {
                    remGranted = await permissionService.requestRemindersAccess()
                }
                if reqNotif {
                    notifGranted = await permissionService.requestNotificationAccess()
                }

                let calPerm = permissionService.calendarStatus().rawValue
                let remPerm = permissionService.remindersStatus().rawValue
                let notifPerm = await permissionService.notificationStatus().rawValue

                return .success(id: request.id, result: AnyCodable([
                    "calendar_granted": calGranted,
                    "reminders_granted": remGranted,
                    "notification_granted": notifGranted,
                    "calendar_permission": calPerm,
                    "reminders_permission": remPerm,
                    "notification_permission": notifPerm,
                ]))

            case "system.start_at_login":
                let action = (request.params["action"]?.value as? String) ?? "status"
                var isEnabled = false
                var statusStr = "unsupported"
                if #available(macOS 13.0, *) {
                    let service = SMAppService.mainApp
                    if action == "enable" {
                        try? service.register()
                    } else if action == "disable" {
                        try? await service.unregister()
                    }
                    isEnabled = (service.status == .enabled)
                    switch service.status {
                    case .notRegistered: statusStr = "not_registered"
                    case .enabled: statusStr = "enabled"
                    case .requiresApproval: statusStr = "requires_approval"
                    case .notFound: statusStr = "not_found"
                    @unknown default: statusStr = "unknown"
                    }
                }
                return .success(id: request.id, result: AnyCodable([
                    "enabled": isEnabled,
                    "status": statusStr,
                ]))

            // MARK: - Calendar APIs
            case "calendar.list_calendars":
                try checkCalendarPermission()
                let cals = calendarService.listCalendars()
                let encoded = try JSONEncoder().encode(cals)
                let jsonObj = try JSONSerialization.jsonObject(with: encoded) as? [Sendable] ?? []
                return .success(id: request.id, result: AnyCodable(["calendars": jsonObj]))

            case "calendar.list_events":
                try checkCalendarPermission()
                guard let startStr = request.params["start"]?.value as? String,
                      let startDate = DateHelper.parse(startStr) else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing or invalid 'start' date")
                }
                guard let endStr = request.params["end"]?.value as? String,
                      let endDate = DateHelper.parse(endStr) else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing or invalid 'end' date")
                }
                let calendarIds = (request.params["calendar_ids"]?.value as? [Sendable])?.compactMap { $0 as? String }
                let limit = request.params["limit"]?.value as? Int

                let events = try calendarService.listEvents(start: startDate, end: endDate, calendarIds: calendarIds, limit: limit)
                let encoded = try JSONEncoder().encode(events)
                let jsonObj = try JSONSerialization.jsonObject(with: encoded) as? [Sendable] ?? []
                return .success(id: request.id, result: AnyCodable(["events": jsonObj]))

            case "calendar.get_event":
                try checkCalendarPermission()
                guard let eventId = request.params["event_id"]?.value as? String ?? request.params["id"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'event_id'")
                }
                let event = try calendarService.getEvent(id: eventId)
                let encoded = try JSONEncoder().encode(event)
                let jsonObj = try JSONSerialization.jsonObject(with: encoded) as? [String: Sendable] ?? [:]
                return .success(id: request.id, result: AnyCodable(jsonObj))

            case "calendar.create_event":
                try checkCalendarPermission()
                guard let title = request.params["title"]?.value as? String, !title.isEmpty else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'title'")
                }
                guard let startStr = request.params["start"]?.value as? String,
                      let startDate = DateHelper.parse(startStr) else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing or invalid 'start' date")
                }
                guard let endStr = request.params["end"]?.value as? String,
                      let endDate = DateHelper.parse(endStr) else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing or invalid 'end' date")
                }
                let calendarId = request.params["calendar_id"]?.value as? String
                let notes = request.params["notes"]?.value as? String
                let location = request.params["location"]?.value as? String
                let alarmMinutes = request.params["alarm_minutes_before"]?.value as? Int

                let event = try calendarService.createEvent(
                    title: title,
                    start: startDate,
                    end: endDate,
                    calendarId: calendarId,
                    notes: notes,
                    location: location,
                    alarmMinutesBefore: alarmMinutes
                )
                let encoded = try JSONEncoder().encode(event)
                let jsonObj = try JSONSerialization.jsonObject(with: encoded) as? [String: Sendable] ?? [:]
                return .success(id: request.id, result: AnyCodable(jsonObj))

            case "calendar.update_event":
                try checkCalendarPermission()
                guard let eventId = request.params["event_id"]?.value as? String ?? request.params["id"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'event_id'")
                }
                let scopeStr = request.params["recurrence_scope"]?.value as? String
                let scope = scopeStr.flatMap { RecurrenceScope(rawValue: $0) }
                let title = request.params["title"]?.value as? String
                let startStr = request.params["start"]?.value as? String
                let startDate = startStr.flatMap { DateHelper.parse($0) }
                let endStr = request.params["end"]?.value as? String
                let endDate = endStr.flatMap { DateHelper.parse($0) }
                let notes = request.params["notes"]?.value as? String
                let location = request.params["location"]?.value as? String

                let updated = try calendarService.updateEvent(
                    id: eventId,
                    recurrenceScope: scope,
                    title: title,
                    start: startDate,
                    end: endDate,
                    notes: notes,
                    location: location
                )
                let encoded = try JSONEncoder().encode(updated)
                let jsonObj = try JSONSerialization.jsonObject(with: encoded) as? [String: Sendable] ?? [:]
                return .success(id: request.id, result: AnyCodable(jsonObj))

            case "calendar.delete_event":
                try checkCalendarPermission()
                guard let eventId = request.params["event_id"]?.value as? String ?? request.params["id"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'event_id'")
                }
                let scopeStr = request.params["recurrence_scope"]?.value as? String
                let scope = scopeStr.flatMap { RecurrenceScope(rawValue: $0) }

                let deleted = try calendarService.deleteEvent(id: eventId, recurrenceScope: scope)
                return .success(id: request.id, result: AnyCodable(["deleted": deleted, "id": eventId]))

            // MARK: - Reminders APIs
            case "reminders.list_lists":
                try checkRemindersPermission()
                let lists = reminderService.listLists()
                let encoded = try JSONEncoder().encode(lists)
                let jsonObj = try JSONSerialization.jsonObject(with: encoded) as? [Sendable] ?? []
                return .success(id: request.id, result: AnyCodable(["lists": jsonObj]))

            case "reminders.list":
                try checkRemindersPermission()
                let listId = request.params["list_id"]?.value as? String
                let completed = request.params["completed"]?.value as? Bool
                let dueBeforeStr = request.params["due_before"]?.value as? String
                let dueBefore = dueBeforeStr.flatMap { DateHelper.parse($0) }
                let dueAfterStr = request.params["due_after"]?.value as? String
                let dueAfter = dueAfterStr.flatMap { DateHelper.parse($0) }
                let limit = request.params["limit"]?.value as? Int

                let reminders = try await reminderService.listReminders(
                    listId: listId,
                    completed: completed,
                    dueBefore: dueBefore,
                    dueAfter: dueAfter,
                    limit: limit
                )
                let encoded = try JSONEncoder().encode(reminders)
                let jsonObj = try JSONSerialization.jsonObject(with: encoded) as? [Sendable] ?? []
                return .success(id: request.id, result: AnyCodable(["reminders": jsonObj]))

            case "reminders.get":
                try checkRemindersPermission()
                guard let id = request.params["reminder_id"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing reminder_id")
                }
                let reminder = try reminderService.getReminder(id: id)
                let data = try JSONEncoder().encode(reminder)
                let object = try JSONSerialization.jsonObject(with: data) as? [String: Sendable] ?? [:]
                return .success(id: request.id, result: AnyCodable(object))

            case "reminders.create":
                try checkRemindersPermission()
                guard let title = request.params["title"]?.value as? String, !title.isEmpty else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'title'")
                }
                let notes = request.params["notes"]?.value as? String
                let dueAtStr = request.params["due_at"]?.value as? String
                let dueAt = dueAtStr.flatMap { DateHelper.parse($0) }
                let listId = request.params["list_id"]?.value as? String
                let priority = request.params["priority"]?.value as? Int
                let alarmStr = request.params["alarm"]?.value as? String
                let alarm = alarmStr.flatMap { DateHelper.parse($0) }

                let reminder = try reminderService.createReminder(
                    title: title,
                    notes: notes,
                    dueAt: dueAt,
                    listId: listId,
                    priority: priority,
                    alarm: alarm
                )
                let encoded = try JSONEncoder().encode(reminder)
                let jsonObj = try JSONSerialization.jsonObject(with: encoded) as? [String: Sendable] ?? [:]
                return .success(id: request.id, result: AnyCodable(jsonObj))

            case "reminders.update":
                try checkRemindersPermission()
                guard let remId = request.params["reminder_id"]?.value as? String ?? request.params["id"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'reminder_id'")
                }
                let title = request.params["title"]?.value as? String
                let notes = request.params["notes"]?.value as? String
                let dueAtStr = request.params["due_at"]?.value as? String
                let dueAt = dueAtStr.flatMap { DateHelper.parse($0) }
                let priority = request.params["priority"]?.value as? Int

                let updated = try reminderService.updateReminder(
                    id: remId,
                    title: title,
                    notes: notes,
                    dueAt: dueAt,
                    priority: priority
                )
                let encoded = try JSONEncoder().encode(updated)
                let jsonObj = try JSONSerialization.jsonObject(with: encoded) as? [String: Sendable] ?? [:]
                return .success(id: request.id, result: AnyCodable(jsonObj))

            case "reminders.complete":
                try checkRemindersPermission()
                guard let remId = request.params["reminder_id"]?.value as? String ?? request.params["id"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'reminder_id'")
                }
                let completed = (request.params["completed"]?.value as? Bool) ?? true
                let res = try reminderService.completeReminder(id: remId, completed: completed)
                let encoded = try JSONEncoder().encode(res)
                let jsonObj = try JSONSerialization.jsonObject(with: encoded) as? [String: Sendable] ?? [:]
                return .success(id: request.id, result: AnyCodable(jsonObj))

            case "reminders.delete":
                try checkRemindersPermission()
                guard let remId = request.params["reminder_id"]?.value as? String ?? request.params["id"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'reminder_id'")
                }
                let deleted = try reminderService.deleteReminder(id: remId)
                return .success(id: request.id, result: AnyCodable(["deleted": deleted, "id": remId]))

            // MARK: - Notifications APIs
            case "notifications.status":
                let st = await notificationService.getStatus()
                return .success(id: request.id, result: AnyCodable(st))

            case "notifications.show":
                guard let title = request.params["title"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'title'")
                }
                guard let body = request.params["body"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'body'")
                }
                let identifier = request.params["identifier"]?.value as? String
                let res = try await notificationService.showNotification(title: title, body: body, identifier: identifier)
                return .success(id: request.id, result: AnyCodable(res))

            case "notifications.schedule":
                guard let title = request.params["title"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'title'")
                }
                guard let body = request.params["body"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'body'")
                }
                let delay = (request.params["delay_seconds"]?.value as? Double) ?? (request.params["delay_seconds"]?.value as? Int).map { Double($0) }
                let schedStr = request.params["scheduled_at"]?.value as? String
                let schedAt = schedStr.flatMap { DateHelper.parse($0) }
                let identifier = request.params["identifier"]?.value as? String

                let res = try await notificationService.scheduleNotification(
                    title: title,
                    body: body,
                    delaySeconds: delay,
                    scheduledAt: schedAt,
                    identifier: identifier
                )
                return .success(id: request.id, result: AnyCodable(res))

            case "notifications.cancel":
                guard let identifier = request.params["identifier"]?.value as? String else {
                    return .failure(id: request.id, code: "INVALID_ARGUMENT", message: "Missing 'identifier'")
                }
                let cancelled = await notificationService.cancelNotification(identifier: identifier)
                return .success(id: request.id, result: AnyCodable(["cancelled": cancelled, "identifier": identifier]))

            default:
                return .failure(id: request.id, code: "METHOD_NOT_FOUND", message: "Method '\(request.method)' is not recognized.")
            }
        } catch let err as CalendarServiceError {
            return .failure(id: request.id, code: err.errorCode, message: err.localizedDescription)
        } catch let err as ReminderServiceError {
            return .failure(id: request.id, code: err.errorCode, message: err.localizedDescription)
        } catch let err as NotificationServiceError {
            return .failure(id: request.id, code: "PERMISSION_DENIED", message: err.localizedDescription)
        } catch {
            return .failure(id: request.id, code: "INTERNAL_ERROR", message: error.localizedDescription)
        }
    }

    private func checkCalendarPermission() throws {
        let status = permissionService.calendarStatus()
        guard status == .fullAccess else {
            throw CalendarServiceError.permissionDenied("Calendar access is not granted (current status: \(status.rawValue)).")
        }
    }

    private func checkRemindersPermission() throws {
        let status = permissionService.remindersStatus()
        guard status == .fullAccess else {
            throw ReminderServiceError.permissionDenied("Reminders access is not granted (current status: \(status.rawValue)).")
        }
    }
}
