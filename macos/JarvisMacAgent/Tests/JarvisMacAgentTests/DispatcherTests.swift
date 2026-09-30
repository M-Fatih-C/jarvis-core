import XCTest
@testable import JarvisMacAgentLib

// MARK: - Mocks for Testing

final class MockPermissionService: PermissionServiceProtocol, @unchecked Sendable {
    var calendarPerm: PermissionStatus = .fullAccess
    var remindersPerm: PermissionStatus = .fullAccess
    var notifPerm: PermissionStatus = .authorized

    func calendarStatus() -> PermissionStatus { calendarPerm }
    func remindersStatus() -> PermissionStatus { remindersPerm }
    func notificationStatus() async -> PermissionStatus { notifPerm }
    func requestCalendarAccess() async -> Bool { true }
    func requestRemindersAccess() async -> Bool { true }
    func requestNotificationAccess() async -> Bool { true }
}

final class MockCalendarService: CalendarServiceProtocol, @unchecked Sendable {
    var events: [CalendarEventDTO] = []
    var shouldFailClarification = false

    func listCalendars() -> [CalendarDTO] {
        [CalendarDTO(id: "cal-1", title: "Main", color: "#FF0000", allows_modifications: true)]
    }

    func listEvents(start: Date, end: Date, calendarIds: [String]?, limit: Int?) throws -> [CalendarEventDTO] {
        events
    }

    func getEvent(id: String) throws -> CalendarEventDTO {
        if let ev = events.first(where: { $0.id == id }) { return ev }
        throw CalendarServiceError.notFound(id)
    }

    func createEvent(
        title: String,
        start: Date,
        end: Date,
        calendarId: String?,
        notes: String?,
        location: String?,
        alarmMinutesBefore: Int?
    ) throws -> CalendarEventDTO {
        let ev = CalendarEventDTO(id: "new-ev-1", calendar_id: calendarId ?? "cal-1", title: title, start: DateHelper.format(start), end: DateHelper.format(end))
        events.append(ev)
        return ev
    }

    func updateEvent(
        id: String,
        recurrenceScope: RecurrenceScope?,
        title: String?,
        start: Date?,
        end: Date?,
        notes: String?,
        location: String?
    ) throws -> CalendarEventDTO {
        if shouldFailClarification {
            throw CalendarServiceError.clarificationRequired("Recurring event modification requires explicit recurrence_scope")
        }
        return CalendarEventDTO(id: id, calendar_id: "cal-1", title: title ?? "Updated", start: "2026-10-01T10:00:00Z", end: "2026-10-01T11:00:00Z")
    }

    func deleteEvent(id: String, recurrenceScope: RecurrenceScope?) throws -> Bool {
        true
    }
}

final class MockReminderService: ReminderServiceProtocol, @unchecked Sendable {
    var reminders: [ReminderDTO] = []

    func listLists() -> [ReminderListDTO] {
        [ReminderListDTO(id: "list-1", title: "Reminders", color: "#0000FF")]
    }

    func listReminders(listId: String?, completed: Bool?, dueBefore: Date?, dueAfter: Date?, limit: Int?) async throws -> [ReminderDTO] {
        reminders
    }

    func getReminder(id: String) throws -> ReminderDTO {
        if let r = reminders.first(where: { $0.id == id }) { return r }
        throw ReminderServiceError.notFound(id)
    }

    func createReminder(title: String, notes: String?, dueAt: Date?, listId: String?, priority: Int?, alarm: Date?) throws -> ReminderDTO {
        let r = ReminderDTO(id: "rem-1", list_id: listId ?? "list-1", title: title, notes: notes, completed: false, due_at: dueAt.map { DateHelper.format($0) }, priority: priority ?? 0)
        reminders.append(r)
        return r
    }

    func updateReminder(id: String, title: String?, notes: String?, dueAt: Date?, priority: Int?) throws -> ReminderDTO {
        ReminderDTO(id: id, list_id: "list-1", title: title ?? "Updated", notes: notes, completed: false, due_at: nil, priority: priority ?? 0)
    }

    func completeReminder(id: String, completed: Bool) throws -> ReminderDTO {
        ReminderDTO(id: id, list_id: "list-1", title: "Completed Task", notes: nil, completed: completed, due_at: nil, priority: 0)
    }

    func deleteReminder(id: String) throws -> Bool {
        true
    }
}

final class MockNotificationService: NotificationServiceProtocol, @unchecked Sendable {
    func getStatus() async -> [String: Sendable] {
        ["status": "authorized", "notifications_enabled": true]
    }

    func showNotification(title: String, body: String, identifier: String?) async throws -> [String: Sendable] {
        ["delivered": true, "identifier": identifier ?? "test-id", "title": title, "body": body]
    }

    func scheduleNotification(title: String, body: String, delaySeconds: Double?, scheduledAt: Date?, identifier: String?) async throws -> [String: Sendable] {
        ["scheduled": true, "identifier": identifier ?? "test-id", "title": title, "body": body]
    }

    func cancelNotification(identifier: String) async -> Bool {
        true
    }
}

final class DispatcherTests: XCTestCase {
    var dispatcher: RPCDispatcher!
    var mockPerms: MockPermissionService!
    var mockCal: MockCalendarService!
    var mockRem: MockReminderService!
    var mockNotif: MockNotificationService!

    override func setUp() {
        super.setUp()
        mockPerms = MockPermissionService()
        mockCal = MockCalendarService()
        mockRem = MockReminderService()
        mockNotif = MockNotificationService()
        dispatcher = RPCDispatcher(
            calendarService: mockCal,
            reminderService: mockRem,
            notificationService: mockNotif,
            permissionService: mockPerms
        )
    }

    func testSystemHealthDispatch() async {
        let req = RPCRequest(method: "system.health")
        let resp = await dispatcher.dispatch(request: req)

        XCTAssertTrue(resp.ok)
        let dict = resp.result?.value as? [String: Sendable]
        XCTAssertEqual(dict?["agent"] as? String, "ready")
        XCTAssertEqual(dict?["calendar_permission"] as? String, "full_access")
        XCTAssertEqual(dict?["reminders_permission"] as? String, "full_access")
        XCTAssertEqual(dict?["notification_permission"] as? String, "authorized")
    }

    func testCalendarPermissionDenied() async {
        mockPerms.calendarPerm = .denied

        let req = RPCRequest(
            method: "calendar.list_events",
            params: [
                "start": AnyCodable("2026-10-01T00:00:00Z"),
                "end": AnyCodable("2026-10-02T00:00:00Z")
            ]
        )
        let resp = await dispatcher.dispatch(request: req)

        XCTAssertFalse(resp.ok)
        XCTAssertEqual(resp.error?.code, "PERMISSION_DENIED")
    }

    func testCalendarListEventsSuccess() async {
        mockCal.events = [
            CalendarEventDTO(id: "e-1", calendar_id: "c-1", title: "Meeting", start: "2026-10-01T10:00:00Z", end: "2026-10-01T11:00:00Z")
        ]

        let req = RPCRequest(
            method: "calendar.list_events",
            params: [
                "start": AnyCodable("2026-10-01T00:00:00Z"),
                "end": AnyCodable("2026-10-02T00:00:00Z")
            ]
        )
        let resp = await dispatcher.dispatch(request: req)

        XCTAssertTrue(resp.ok)
        let dict = resp.result?.value as? [String: Sendable]
        let evs = dict?["events"] as? [[String: Sendable]]
        XCTAssertEqual(evs?.count, 1)
        XCTAssertEqual(evs?[0]["title"] as? String, "Meeting")
    }

    func testRemindersPermissionDenied() async {
        mockPerms.remindersPerm = .denied

        let req = RPCRequest(
            method: "reminders.create",
            params: ["title": AnyCodable("Buy Milk")]
        )
        let resp = await dispatcher.dispatch(request: req)

        XCTAssertFalse(resp.ok)
        XCTAssertEqual(resp.error?.code, "PERMISSION_DENIED")
    }

    func testRecurringEventClarificationRequired() async {
        mockCal.shouldFailClarification = true

        let req = RPCRequest(
            method: "calendar.update_event",
            params: [
                "event_id": AnyCodable("ev-recur-1"),
                "title": AnyCodable("New Title")
            ]
        )
        let resp = await dispatcher.dispatch(request: req)

        XCTAssertFalse(resp.ok)
        XCTAssertEqual(resp.error?.code, "CLARIFICATION_REQUIRED")
    }

    func testMethodNotFound() async {
        let req = RPCRequest(method: "unsupported.arbitrary_command")
        let resp = await dispatcher.dispatch(request: req)

        XCTAssertFalse(resp.ok)
        XCTAssertEqual(resp.error?.code, "METHOD_NOT_FOUND")
    }
}
