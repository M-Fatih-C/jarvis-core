import Foundation
import EventKit
import AppKit

public enum ReminderServiceError: Error, LocalizedError {
    case notFound(String)
    case invalidArgument(String)
    case permissionDenied(String)
    case saveFailed(String)
    case deleteFailed(String)
    case listNotWritable(String)

    public var errorDescription: String? {
        switch self {
        case .notFound(let msg): return "Reminder not found: \(msg)"
        case .invalidArgument(let msg): return "Invalid argument: \(msg)"
        case .permissionDenied(let msg): return msg
        case .saveFailed(let msg): return "Failed to save reminder: \(msg)"
        case .deleteFailed(let msg): return "Failed to delete reminder: \(msg)"
        case .listNotWritable(let msg): return "Reminder list is not writable: \(msg)"
        }
    }

    public var errorCode: String {
        switch self {
        case .notFound: return "NOT_FOUND"
        case .invalidArgument: return "INVALID_ARGUMENT"
        case .permissionDenied: return "PERMISSION_DENIED"
        case .saveFailed: return "SAVE_FAILED"
        case .deleteFailed: return "DELETE_FAILED"
        case .listNotWritable: return "LIST_NOT_WRITABLE"
        }
    }
}

public protocol ReminderServiceProtocol: Sendable {
    func listLists() -> [ReminderListDTO]
    func listReminders(
        listId: String?,
        completed: Bool?,
        dueBefore: Date?,
        dueAfter: Date?,
        limit: Int?
    ) async throws -> [ReminderDTO]
    func getReminder(id: String) throws -> ReminderDTO
    func createReminder(
        title: String,
        notes: String?,
        dueAt: Date?,
        listId: String?,
        priority: Int?,
        alarm: Date?
    ) throws -> ReminderDTO
    func updateReminder(
        id: String,
        title: String?,
        notes: String?,
        dueAt: Date?,
        priority: Int?
    ) throws -> ReminderDTO
    func completeReminder(id: String, completed: Bool) throws -> ReminderDTO
    func deleteReminder(id: String) throws -> Bool
}

public final class ReminderService: ReminderServiceProtocol, @unchecked Sendable {
    private let eventStore: EKEventStore

    public init(eventStore: EKEventStore) {
        self.eventStore = eventStore
    }

    public func listLists() -> [ReminderListDTO] {
        let lists = eventStore.calendars(for: .reminder)
        return lists.map { list in
            ReminderListDTO(
                id: list.calendarIdentifier,
                title: list.title,
                color: list.color.hexString
            )
        }
    }

    public func listReminders(
        listId: String?,
        completed: Bool?,
        dueBefore: Date?,
        dueAfter: Date?,
        limit: Int?
    ) async throws -> [ReminderDTO] {
        var calendars: [EKCalendar]? = nil
        if let lId = listId, !lId.isEmpty {
            let all = eventStore.calendars(for: .reminder)
            guard let found = all.first(where: { $0.calendarIdentifier == lId }) else {
                throw ReminderServiceError.notFound("Reminder list '\(lId)' not found")
            }
            calendars = [found]
        }

        let predicate = eventStore.predicateForReminders(in: calendars)
        let reminders: [EKReminder] = await withCheckedContinuation { continuation in
            eventStore.fetchReminders(matching: predicate) { items in
                continuation.resume(returning: items ?? [])
            }
        }

        var filtered = reminders

        if let comp = completed {
            filtered = filtered.filter { $0.isCompleted == comp }
        }

        if let before = dueBefore {
            filtered = filtered.filter { item in
                guard let due = item.dueDateComponents?.date else { return false }
                return due <= before
            }
        }

        if let after = dueAfter {
            filtered = filtered.filter { item in
                guard let due = item.dueDateComponents?.date else { return false }
                return due >= after
            }
        }

        // Sort by dueDate or creation
        filtered.sort { (a, b) -> Bool in
            let dateA = a.dueDateComponents?.date ?? Date.distantFuture
            let dateB = b.dueDateComponents?.date ?? Date.distantFuture
            return dateA < dateB
        }

        let limited: [EKReminder]
        if let lim = limit, lim > 0 {
            limited = Array(filtered.prefix(lim))
        } else {
            limited = filtered
        }

        return limited.map { toDTO($0) }
    }

    public func getReminder(id: String) throws -> ReminderDTO {
        guard let item = eventStore.calendarItem(withIdentifier: id) as? EKReminder else {
            throw ReminderServiceError.notFound(id)
        }
        return toDTO(item)
    }

    public func createReminder(
        title: String,
        notes: String?,
        dueAt: Date?,
        listId: String?,
        priority: Int?,
        alarm: Date?
    ) throws -> ReminderDTO {
        let targetList: EKCalendar
        if let lId = listId {
            guard let found = eventStore.calendars(for: .reminder).first(where: { $0.calendarIdentifier == lId }) else {
                throw ReminderServiceError.notFound("Reminder list '\(lId)' not found")
            }
            guard found.allowsContentModifications else {
                throw ReminderServiceError.listNotWritable("Reminder list '\(found.title)' is read-only")
            }
            targetList = found
        } else if let defaultList = eventStore.defaultCalendarForNewReminders(), defaultList.allowsContentModifications {
            targetList = defaultList
        } else if let fallback = eventStore.calendars(for: .reminder).first(where: { $0.allowsContentModifications }) {
            targetList = fallback
        } else {
            throw ReminderServiceError.listNotWritable("No writable reminder list available")
        }

        let reminder = EKReminder(eventStore: eventStore)
        reminder.calendar = targetList
        reminder.title = title
        reminder.notes = notes

        if let prio = priority {
            reminder.priority = prio
        }

        if let due = dueAt {
            var cal = Calendar(identifier: .gregorian)
            cal.timeZone = TimeZone.current
            reminder.dueDateComponents = cal.dateComponents([.year, .month, .day, .hour, .minute, .second, .timeZone], from: due)
        }

        if let alarmDate = alarm {
            reminder.addAlarm(EKAlarm(absoluteDate: alarmDate))
        }

        do {
            try eventStore.save(reminder, commit: true)
        } catch {
            throw ReminderServiceError.saveFailed(error.localizedDescription)
        }

        return toDTO(reminder)
    }

    public func updateReminder(
        id: String,
        title: String?,
        notes: String?,
        dueAt: Date?,
        priority: Int?
    ) throws -> ReminderDTO {
        guard let item = eventStore.calendarItem(withIdentifier: id) as? EKReminder else {
            throw ReminderServiceError.notFound(id)
        }

        if let title = title { item.title = title }
        if let notes = notes { item.notes = notes }
        if let priority = priority { item.priority = priority }

        if let due = dueAt {
            var cal = Calendar(identifier: .gregorian)
            cal.timeZone = TimeZone.current
            item.dueDateComponents = cal.dateComponents([.year, .month, .day, .hour, .minute, .second, .timeZone], from: due)
        }

        do {
            try eventStore.save(item, commit: true)
        } catch {
            throw ReminderServiceError.saveFailed(error.localizedDescription)
        }

        return toDTO(item)
    }

    public func completeReminder(id: String, completed: Bool) throws -> ReminderDTO {
        guard let item = eventStore.calendarItem(withIdentifier: id) as? EKReminder else {
            throw ReminderServiceError.notFound(id)
        }

        item.isCompleted = completed
        item.completionDate = completed ? Date() : nil

        do {
            try eventStore.save(item, commit: true)
        } catch {
            throw ReminderServiceError.saveFailed(error.localizedDescription)
        }

        return toDTO(item)
    }

    public func deleteReminder(id: String) throws -> Bool {
        guard let item = eventStore.calendarItem(withIdentifier: id) as? EKReminder else {
            throw ReminderServiceError.notFound(id)
        }

        do {
            try eventStore.remove(item, commit: true)
            return true
        } catch {
            throw ReminderServiceError.deleteFailed(error.localizedDescription)
        }
    }

    private func toDTO(_ reminder: EKReminder) -> ReminderDTO {
        let dueStr: String?
        if let comps = reminder.dueDateComponents, let date = comps.date {
            dueStr = DateHelper.format(date)
        } else {
            dueStr = nil
        }

        return ReminderDTO(
            id: reminder.calendarItemIdentifier,
            list_id: reminder.calendar?.calendarIdentifier ?? "",
            title: reminder.title ?? "",
            notes: reminder.notes,
            completed: reminder.isCompleted,
            due_at: dueStr,
            priority: reminder.priority
        )
    }
}

private extension NSColor {
    var hexString: String {
        guard let rgb = usingColorSpace(.sRGB) else { return "#007AFF" }
        let r = Int(rgb.redComponent * 255.0)
        let g = Int(rgb.greenComponent * 255.0)
        let b = Int(rgb.blueComponent * 255.0)
        return String(format: "#%02X%02X%02X", r, g, b)
    }
}
