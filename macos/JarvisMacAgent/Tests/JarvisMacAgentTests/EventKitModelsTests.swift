import XCTest
import EventKit
@testable import JarvisMacAgentLib

final class EventKitModelsTests: XCTestCase {
    func testDateHelperParsingAndFormatting() {
        let isoStr = "2026-10-01T10:00:00+03:00"
        guard let date = DateHelper.parse(isoStr) else {
            XCTFail("Failed to parse date string")
            return
        }

        let formatted = DateHelper.format(date)
        XCTAssertFalse(formatted.isEmpty)

        // Parse formatted back
        let parsedBack = DateHelper.parse(formatted)
        XCTAssertNotNil(parsedBack)
        XCTAssertEqual(date.timeIntervalSince1970, parsedBack!.timeIntervalSince1970, accuracy: 1.0)
    }

    func testRecurrenceScopeMapping() {
        XCTAssertEqual(RecurrenceScope.thisOccurrence.ekSpan, EKSpan.thisEvent)
        XCTAssertEqual(RecurrenceScope.futureOccurrences.ekSpan, EKSpan.futureEvents)
    }
    
    func testCalendarDTOEncoding() throws {
        let dto = CalendarDTO(
            id: "cal-icloud-1",
            title: "Work",
            color: "#007AFF",
            allows_modifications: true,
            source_title: "iCloud",
            source_type: "calDAV"
        )

        let data = try JSONEncoder().encode(dto)
        let decoded = try JSONDecoder().decode(CalendarDTO.self, from: data)

        XCTAssertEqual(decoded, dto)
        XCTAssertEqual(decoded.source_title, "iCloud")
    }

    func testCalendarEventDTOEncoding() throws {
        let dto = CalendarEventDTO(
            id: "ev-101",
            calendar_id: "cal-primary",
            title: "Sprint Planning",
            start: "2026-10-01T10:00:00Z",
            end: "2026-10-01T11:00:00Z",
            all_day: false,
            location: "Room 404",
            notes: "Bring laptop",
            availability: "busy"
        )

        let data = try JSONEncoder().encode(dto)
        let decoded = try JSONDecoder().decode(CalendarEventDTO.self, from: data)

        XCTAssertEqual(decoded, dto)
    }

    func testReminderDTOEncoding() throws {
        let dto = ReminderDTO(
            id: "rem-202",
            list_id: "list-tasks",
            title: "Study YBS exam",
            notes: "Chapter 3-4",
            completed: false,
            due_at: "2026-10-01T19:00:00+03:00",
            priority: 1
        )

        let data = try JSONEncoder().encode(dto)
        let decoded = try JSONDecoder().decode(ReminderDTO.self, from: data)

        XCTAssertEqual(decoded, dto)
    }
}
