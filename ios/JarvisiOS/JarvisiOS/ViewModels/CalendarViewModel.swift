import Foundation
import SwiftUI

@MainActor
public final class CalendarViewModel: ObservableObject {
    @Published public var calendars: [iOSCalendarInfoDTO] = []
    @Published public var selectedCalendar: iOSCalendarInfoDTO? = nil
    @Published public var selectedDate: Date = Date() {
        didSet {
            Task {
                await loadEvents(for: selectedDate)
            }
        }
    }
    @Published public var events: [iOSCalendarEventDTO] = []
    @Published public var isLoading: Bool = false
    @Published public var errorMessage: String? = nil

    private let apiService = JarvisAPIService.shared

    public init() {
        Task {
            await loadCalendars()
            await loadEvents(for: selectedDate)
        }
    }

    public func loadCalendars() async {
        isLoading = true
        errorMessage = nil
        do {
            calendars = try await apiService.fetchCalendars()
            selectedCalendar = calendars.first(where: { $0.is_icloud }) ?? calendars.first
        } catch {
            errorMessage = "Takvim bilgisi alınamadı: \(error.localizedDescription)"
        }
        isLoading = false
    }

    public func loadEvents(for date: Date) async {
        isLoading = true
        errorMessage = nil

        let calendar = Calendar.current
        let startOfDay = calendar.startOfDay(for: date)
        guard let endOfDay = calendar.date(byAdding: .day, value: 1, to: startOfDay) else {
            isLoading = false
            return
        }

        do {
            let fetched = try await apiService.fetchEvents(
                calendarId: selectedCalendar?.id,
                start: startOfDay,
                end: endOfDay
            )
            self.events = fetched
        } catch {
            // Real empty state if service not reachable
            self.events = []
            self.errorMessage = "Etkinlikler getirilemedi: \(error.localizedDescription)"
        }
        isLoading = false
    }
}
