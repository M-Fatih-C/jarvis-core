import Foundation
import SwiftUI

@MainActor
public final class CalendarViewModel: ObservableObject {
    @Published public var calendars: [iOSCalendarInfoDTO] = []
    @Published public var selectedCalendarId: String?
    @Published public var selectedDate = Date()
    @Published public var events: [iOSCalendarEventDTO] = []
    @Published public var isLoading = false
    @Published public var errorMessage: String?
    private var requestID = UUID()
    private var loadedOwner: String?

    public init() {}

    public func refresh(owner: String, reloadCalendars: Bool = false) async {
        let id = UUID()
        requestID = id
        if loadedOwner != owner {
            calendars = []; events = []; selectedCalendarId = nil
        }
        guard !owner.isEmpty else {
            loadedOwner = nil
            isLoading = false
            errorMessage = "Takvimini görmek için Ayarlar’dan giriş yap."
            return
        }
        isLoading = true
        errorMessage = nil
        let date = selectedDate
        let calendarId = selectedCalendarId
        do {
            if reloadCalendars || loadedOwner != owner {
                let fetched = try await JarvisAPIService.shared.fetchCalendars()
                guard requestID == id, !Task.isCancelled else { return }
                calendars = fetched
                loadedOwner = owner
            }
            let start = CalendarDates.calendar.startOfDay(for: date)
            let end = CalendarDates.calendar.date(byAdding: .day, value: 1, to: start)!
            let fetched = try await JarvisAPIService.shared.fetchEvents(calendarId: calendarId, start: start, end: end)
            guard requestID == id, !Task.isCancelled else { return }
            events = fetched.sorted { (CalendarDates.parse($0.start) ?? .distantPast) < (CalendarDates.parse($1.start) ?? .distantPast) }
        } catch {
            guard requestID == id, !Task.isCancelled else { return }
            events = []
            errorMessage = "Takvim alınamadı. Mac’in açık ve internete bağlı olduğundan emin ol. \(error.localizedDescription)"
        }
        if requestID == id { isLoading = false }
    }
}
