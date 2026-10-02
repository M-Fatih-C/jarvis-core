import Foundation
import SwiftUI

@MainActor
public final class CalendarViewModel: ObservableObject {
    @Published public var calendars: [iOSCalendarInfoDTO] = []
    @Published public var selectedCalendar: iOSCalendarInfoDTO? = nil
    @Published public var selectedDate: Date = Date()
    @Published public var isLoading: Bool = false
    @Published public var errorMessage: String? = nil

    private let apiService = JarvisAPIService.shared

    public init() {
        Task {
            await loadCalendars()
        }
    }

    public func loadCalendars() async {
        isLoading = true
        errorMessage = nil
        do {
            calendars = try await apiService.fetchCalendars()
            selectedCalendar = calendars.first(where: { $0.is_icloud }) ?? calendars.first
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }
}
