import Foundation

public struct iOSCalendarEventDTO: Identifiable, Codable, Sendable {
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

public struct iOSCalendarInfoDTO: Identifiable, Codable, Sendable {
    public let id: String
    public let title: String
    public let is_writable: Bool
    public let is_icloud: Bool
    public let is_on_my_mac: Bool
    public let source_title: String?
}
