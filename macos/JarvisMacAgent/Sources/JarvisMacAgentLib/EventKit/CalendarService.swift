import Foundation
import EventKit
import AppKit

public enum CalendarServiceError: Error, LocalizedError {
    case notFound(String)
    case invalidArgument(String)
    case clarificationRequired(String)
    case permissionDenied(String)
    case saveFailed(String)
    case deleteFailed(String)
    case calendarNotWritable(String)

    public var errorDescription: String? {
        switch self {
        case .notFound(let msg): return "Event not found: \(msg)"
        case .invalidArgument(let msg): return "Invalid argument: \(msg)"
        case .clarificationRequired(let msg): return msg
        case .permissionDenied(let msg): return msg
        case .saveFailed(let msg): return "Failed to save event: \(msg)"
        case .deleteFailed(let msg): return "Failed to delete event: \(msg)"
        case .calendarNotWritable(let msg): return "Calendar is not writable: \(msg)"
        }
    }

    public var errorCode: String {
        switch self {
        case .notFound: return "NOT_FOUND"
        case .invalidArgument: return "INVALID_ARGUMENT"
        case .clarificationRequired: return "CLARIFICATION_REQUIRED"
        case .permissionDenied: return "PERMISSION_DENIED"
        case .saveFailed: return "SAVE_FAILED"
        case .deleteFailed: return "DELETE_FAILED"
        case .calendarNotWritable: return "CALENDAR_NOT_WRITABLE"
        }
    }
}

public protocol CalendarServiceProtocol: Sendable {
    func listCalendars() -> [CalendarDTO]
    func listEvents(start: Date, end: Date, calendarIds: [String]?, limit: Int?) throws -> [CalendarEventDTO]
    func getEvent(id: String) throws -> CalendarEventDTO
    func createEvent(
        title: String,
        start: Date,
        end: Date,
        calendarId: String?,
        notes: String?,
        location: String?,
        alarmMinutesBefore: Int?
    ) throws -> CalendarEventDTO
    func updateEvent(
        id: String,
        recurrenceScope: RecurrenceScope?,
        title: String?,
        start: Date?,
        end: Date?,
        notes: String?,
        location: String?
    ) throws -> CalendarEventDTO
    func deleteEvent(id: String, recurrenceScope: RecurrenceScope?) throws -> Bool
}

public final class CalendarService: CalendarServiceProtocol, @unchecked Sendable {
    private let eventStore: EKEventStore

    public init(eventStore: EKEventStore) {
        self.eventStore = eventStore
    }

    public func listCalendars() -> [CalendarDTO] {
        let calendars = eventStore.calendars(for: .event)
        return calendars.map { cal in
            let hex = cal.color.hexString
            let sourceTitle = cal.source?.title
            let sourceType = cal.source != nil ? "\(cal.source.sourceType.rawValue)" : nil
            return CalendarDTO(
                id: cal.calendarIdentifier,
                title: cal.title,
                color: hex,
                allows_modifications: cal.allowsContentModifications,
                source_title: sourceTitle,
                source_type: sourceType
            )
        }
    }

    public func listEvents(start: Date, end: Date, calendarIds: [String]?, limit: Int?) throws -> [CalendarEventDTO] {
        guard end >= start else {
            throw CalendarServiceError.invalidArgument("end date must be greater than or equal to start date")
        }

        var calendars: [EKCalendar]? = nil
        if let ids = calendarIds, !ids.isEmpty {
            let all = eventStore.calendars(for: .event)
            calendars = all.filter { ids.contains($0.calendarIdentifier) }
        }

        let predicate = eventStore.predicateForEvents(withStart: start, end: end, calendars: calendars)
        let events = eventStore.events(matching: predicate).sorted { $0.startDate < $1.startDate }

        let limitedEvents: [EKEvent]
        if let lim = limit, lim > 0 {
            limitedEvents = Array(events.prefix(lim))
        } else {
            limitedEvents = events
        }

        return limitedEvents.map { toDTO($0) }
    }

    public func getEvent(id: String) throws -> CalendarEventDTO {
        guard let event = eventStore.event(withIdentifier: id) else {
            throw CalendarServiceError.notFound(id)
        }
        return toDTO(event)
    }

    public func createEvent(
        title: String,
        start: Date,
        end: Date,
        calendarId: String?,
        notes: String?,
        location: String?,
        alarmMinutesBefore: Int?
    ) throws -> CalendarEventDTO {
        guard end > start else {
            throw CalendarServiceError.invalidArgument("end date must be strictly after start date")
        }

        let targetCalendar: EKCalendar
        if let calId = calendarId {
            guard let found = eventStore.calendars(for: .event).first(where: { $0.calendarIdentifier == calId }) else {
                throw CalendarServiceError.notFound("Calendar with id '\(calId)' not found")
            }
            guard found.allowsContentModifications else {
                throw CalendarServiceError.calendarNotWritable("Calendar '\(found.title)' is read-only")
            }
            targetCalendar = found
        } else if let defaultCal = eventStore.defaultCalendarForNewEvents, defaultCal.allowsContentModifications {
            targetCalendar = defaultCal
        } else if let fallback = eventStore.calendars(for: .event).first(where: { $0.allowsContentModifications }) {
            targetCalendar = fallback
        } else {
            throw CalendarServiceError.calendarNotWritable("No writable calendar available")
        }

        let event = EKEvent(eventStore: eventStore)
        event.calendar = targetCalendar
        event.title = title
        event.startDate = start
        event.endDate = end
        event.notes = notes
        event.location = location

        if let alarmOffset = alarmMinutesBefore, alarmOffset >= 0 {
            event.addAlarm(EKAlarm(relativeOffset: -Double(alarmOffset * 60)))
        }

        do {
            try eventStore.save(event, span: .thisEvent, commit: true)
        } catch {
            throw CalendarServiceError.saveFailed(error.localizedDescription)
        }

        return toDTO(event)
    }

    public func updateEvent(
        id: String,
        recurrenceScope: RecurrenceScope?,
        title: String?,
        start: Date?,
        end: Date?,
        notes: String?,
        location: String?
    ) throws -> CalendarEventDTO {
        guard let event = eventStore.event(withIdentifier: id) else {
            throw CalendarServiceError.notFound(id)
        }

        if event.hasRecurrenceRules && recurrenceScope == nil {
            throw CalendarServiceError.clarificationRequired(
                "Recurring event modification requires explicit recurrence_scope (THIS_OCCURRENCE or FUTURE_OCCURRENCES)."
            )
        }

        let span = recurrenceScope?.ekSpan ?? .thisEvent

        if let title = title { event.title = title }
        if let start = start { event.startDate = start }
        if let end = end { event.endDate = end }
        if let notes = notes { event.notes = notes }
        if let location = location { event.location = location }

        if event.endDate <= event.startDate {
            throw CalendarServiceError.invalidArgument("end date must be strictly after start date")
        }

        do {
            try eventStore.save(event, span: span, commit: true)
        } catch {
            throw CalendarServiceError.saveFailed(error.localizedDescription)
        }

        return toDTO(event)
    }

    public func deleteEvent(id: String, recurrenceScope: RecurrenceScope?) throws -> Bool {
        guard let event = eventStore.event(withIdentifier: id) else {
            throw CalendarServiceError.notFound(id)
        }

        if event.hasRecurrenceRules && recurrenceScope == nil {
            throw CalendarServiceError.clarificationRequired(
                "Recurring event deletion requires explicit recurrence_scope (THIS_OCCURRENCE or FUTURE_OCCURRENCES)."
            )
        }

        let span = recurrenceScope?.ekSpan ?? .thisEvent

        do {
            try eventStore.remove(event, span: span, commit: true)
            return true
        } catch {
            throw CalendarServiceError.deleteFailed(error.localizedDescription)
        }
    }

    private func toDTO(_ event: EKEvent) -> CalendarEventDTO {
        let availabilityStr: String
        switch event.availability {
        case .free: availabilityStr = "free"
        case .busy: availabilityStr = "busy"
        case .tentative: availabilityStr = "tentative"
        case .unavailable, .notSupported: availabilityStr = "unavailable"
        @unknown default: availabilityStr = "busy"
        }

        return CalendarEventDTO(
            id: event.eventIdentifier ?? "",
            calendar_id: event.calendar?.calendarIdentifier ?? "",
            title: event.title ?? "",
            start: DateHelper.format(event.startDate),
            end: DateHelper.format(event.endDate),
            all_day: event.isAllDay,
            location: event.location,
            notes: event.notes,
            availability: availabilityStr
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
