import Foundation
import EventKit

public enum RecurrenceScope: String, Codable, Sendable {
    case thisOccurrence = "THIS_OCCURRENCE"
    case futureOccurrences = "FUTURE_OCCURRENCES"

    public var ekSpan: EKSpan {
        switch self {
        case .thisOccurrence: return .thisEvent
        case .futureOccurrences: return .futureEvents
        }
    }
}

public struct CalendarDTO: Codable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let color: String
    public let allows_modifications: Bool
    public let source_title: String?
    public let source_type: String?

    public init(
        id: String,
        title: String,
        color: String,
        allows_modifications: Bool,
        source_title: String? = nil,
        source_type: String? = nil
    ) {
        self.id = id
        self.title = title
        self.color = color
        self.allows_modifications = allows_modifications
        self.source_title = source_title
        self.source_type = source_type
    }
}

public struct CalendarEventDTO: Codable, Equatable, Sendable {
    public let id: String
    public let calendar_id: String
    public let title: String
    public let start: String
    public let end: String
    public let all_day: Bool
    public let location: String?
    public let notes: String?
    public let availability: String

    public init(
        id: String,
        calendar_id: String,
        title: String,
        start: String,
        end: String,
        all_day: Bool = false,
        location: String? = nil,
        notes: String? = nil,
        availability: String = "busy"
    ) {
        self.id = id
        self.calendar_id = calendar_id
        self.title = title
        self.start = start
        self.end = end
        self.all_day = all_day
        self.location = location
        self.notes = notes
        self.availability = availability
    }
}

public struct ReminderListDTO: Codable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let color: String

    public init(id: String, title: String, color: String) {
        self.id = id
        self.title = title
        self.color = color
    }
}

public struct ReminderDTO: Codable, Equatable, Sendable {
    public let id: String
    public let list_id: String
    public let title: String
    public let notes: String?
    public let completed: Bool
    public let due_at: String?
    public let priority: Int

    public init(
        id: String,
        list_id: String,
        title: String,
        notes: String? = nil,
        completed: Bool = false,
        due_at: String? = nil,
        priority: Int = 0
    ) {
        self.id = id
        self.list_id = list_id
        self.title = title
        self.notes = notes
        self.completed = completed
        self.due_at = due_at
        self.priority = priority
    }
}

public struct DateHelper {
    public static let isoFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    public static let fallbackIsoFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    public static func parse(_ string: String) -> Date? {
        if let d = isoFormatter.date(from: string) { return d }
        return fallbackIsoFormatter.date(from: string)
    }

    public static func format(_ date: Date) -> String {
        return fallbackIsoFormatter.string(from: date)
    }
}
