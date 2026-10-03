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

public struct iOSCalendarInfoDTO: Identifiable, Decodable, Sendable {
    public let id: String
    public let title: String
    public let is_writable: Bool
    public let is_icloud: Bool
    public let is_on_my_mac: Bool
    public let source_title: String?

    enum CodingKeys: String, CodingKey {
        case id, title, is_writable, is_icloud, is_on_my_mac, source_title
        case allows_modifications, source_type
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        title = try values.decode(String.self, forKey: .title)
        source_title = try values.decodeIfPresent(String.self, forKey: .source_title)
        is_writable = try values.decodeIfPresent(Bool.self, forKey: .allows_modifications)
            ?? values.decodeIfPresent(Bool.self, forKey: .is_writable) ?? false
        is_icloud = try values.decodeIfPresent(Bool.self, forKey: .is_icloud)
            ?? (source_title?.localizedCaseInsensitiveContains("icloud") == true)
        let sourceType = try values.decodeIfPresent(String.self, forKey: .source_type)
        is_on_my_mac = try values.decodeIfPresent(Bool.self, forKey: .is_on_my_mac) ?? (sourceType == "0")
    }
}

enum CalendarDates {
    static var calendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/Istanbul")!
        return calendar
    }

    static func parse(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }

    static func time(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "tr_TR")
        formatter.timeZone = calendar.timeZone
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }
}
