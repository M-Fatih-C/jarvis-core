import SwiftUI

public struct CalendarScheduleView: View {
    @StateObject private var viewModel = CalendarViewModel()

    public init() {}

    public var body: some View {
        NavigationStack {
            List {
                Section(header: Text("Takvim Hedefi")) {
                    if let cal = viewModel.selectedCalendar {
                        HStack {
                            Circle()
                                .fill(Color.blue)
                                .frame(width: 10, height: 10)
                            Text(cal.title)
                                .font(.headline)
                            Spacer()
                            if cal.is_icloud {
                                Text("iCloud")
                                    .font(.caption)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(Color.blue.opacity(0.1))
                                    .cornerRadius(6)
                            }
                        }
                    } else if viewModel.isLoading {
                        ProgressView("Takvim bilgisi sorgulanıyor...")
                    } else {
                        Text("Kullanılabilir takvim bulunamadı veya bağlantı bekleniyor.")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                    }
                }

                Section(header: Text("Tarih Seçimi")) {
                    DatePicker("Görüntülenen Gün", selection: $viewModel.selectedDate, displayedComponents: .date)
                        .datePickerStyle(.graphical)
                }

                Section(header: Text("Planlanan Etkinlikler ve Bloklar")) {
                    if viewModel.isLoading {
                        HStack {
                            Spacer()
                            ProgressView("Etkinlikler yükleniyor...")
                            Spacer()
                        }
                        .padding(.vertical, 12)
                    } else if viewModel.events.isEmpty {
                        VStack(spacing: 8) {
                            Image(systemName: "calendar.badge.clock")
                                .font(.largeTitle)
                                .foregroundColor(.secondary)
                            Text("Seçilen tarihte takvim etkinliği veya planlanmış çalışma bloğu bulunmuyor.")
                                .font(.subheadline)
                                .foregroundColor(.secondary)
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 16)
                    } else {
                        ForEach(viewModel.events) { evt in
                            VStack(alignment: .leading, spacing: 6) {
                                HStack {
                                    Rectangle()
                                        .fill(Color.blue)
                                        .frame(width: 4)
                                    VStack(alignment: .leading, spacing: 2) {
                                        Text(evt.title)
                                            .font(.headline)
                                        Text("\(evt.start) - \(evt.end)")
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                        if let notes = evt.notes, !notes.isEmpty {
                                            Text(notes)
                                                .font(.caption2)
                                                .foregroundColor(.secondary)
                                                .lineLimit(2)
                                        }
                                    }
                                    Spacer()
                                    if evt.all_day {
                                        Text("Tam Gün")
                                            .font(.caption2)
                                            .padding(4)
                                            .background(Color(.tertiarySystemFill))
                                            .cornerRadius(4)
                                    }
                                }
                            }
                            .padding(.vertical, 4)
                        }
                    }
                }
            }
            .navigationTitle("Takvim & Plan")
            .refreshable {
                await viewModel.loadCalendars()
                await viewModel.loadEvents(for: viewModel.selectedDate)
            }
        }
    }
}
