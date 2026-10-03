import SwiftUI

public struct CalendarScheduleView: View {
    @EnvironmentObject private var auth: AuthViewModel
    @StateObject private var viewModel = CalendarViewModel()
    public init() {}

    public var body: some View {
        NavigationStack {
            List {
                Section {
                    Picker("Takvim", selection: $viewModel.selectedCalendarId) {
                        Text("Tüm takvimler").tag(String?.none)
                        ForEach(viewModel.calendars) { calendar in
                            Text(calendar.title).tag(Optional(calendar.id))
                        }
                    }
                    DatePicker("Gün", selection: $viewModel.selectedDate, displayedComponents: .date)
                        .datePickerStyle(.compact)
                } footer: {
                    Text("Mac’teki Apple Takvim kayıtları · Türkiye saati")
                }
                Section("Günün programı") {
                    if viewModel.isLoading {
                        ProgressView("Takvim okunuyor…").padding(.vertical, 16)
                    } else if let error = viewModel.errorMessage {
                        VStack(alignment: .leading, spacing: 12) {
                            Label("Takvime bağlanılamadı", systemImage: "wifi.exclamationmark").font(.headline)
                            Text(error).font(.subheadline).foregroundStyle(.secondary)
                            Button("Tekrar dene") { Task { await refresh() } }
                                .buttonStyle(.bordered).frame(minHeight: 44)
                        }.padding(.vertical, 8)
                    } else if viewModel.events.isEmpty {
                        Label("Bu gün için etkinlik yok", systemImage: "calendar")
                            .foregroundStyle(.secondary).padding(.vertical, 16)
                    } else {
                        ForEach(viewModel.events) { event in
                            HStack(alignment: .top, spacing: 14) {
                                Text(timeLabel(event)).font(.subheadline.monospacedDigit())
                                    .foregroundStyle(.mint).frame(width: 64, alignment: .leading)
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(event.title).font(.headline)
                                    if let calendar = viewModel.calendars.first(where: { $0.id == event.calendar_id }) {
                                        Text(calendar.title).font(.caption).foregroundStyle(.secondary)
                                    }
                                    if let location = event.location, !location.isEmpty {
                                        Label(location, systemImage: "mappin").font(.caption).foregroundStyle(.secondary)
                                    }
                                    if let notes = event.notes, !notes.isEmpty {
                                        Text(notes).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                                    }
                                }
                            }.padding(.vertical, 10)
                        }
                    }
                }
            }
            .environment(\.timeZone, CalendarDates.calendar.timeZone)
            .environment(\.locale, Locale(identifier: "tr_TR"))
            .navigationTitle("Takvim")
            .toolbar { Button("Bugün") { viewModel.selectedDate = Date() } }
            .task(id: "\(auth.userId)|\(viewModel.selectedDate.timeIntervalSince1970)|\(viewModel.selectedCalendarId ?? "all")") {
                await viewModel.refresh(owner: auth.userId)
            }
            .refreshable { await refresh() }
        }
    }

    private func refresh() async { await viewModel.refresh(owner: auth.userId, reloadCalendars: true) }

    private func timeLabel(_ event: iOSCalendarEventDTO) -> String {
        if event.all_day { return "Tüm gün" }
        guard let start = CalendarDates.parse(event.start), let end = CalendarDates.parse(event.end) else { return "—" }
        return "\(CalendarDates.time(start))\n\(CalendarDates.time(end))"
    }
}
