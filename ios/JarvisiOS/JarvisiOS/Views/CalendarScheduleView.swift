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
                    } else {
                        Text("Yazılabilir takvim aranıyor...")
                            .foregroundColor(.secondary)
                    }
                }

                Section(header: Text("Tarih Seçimi")) {
                    DatePicker("Görüntülenen Gün", selection: $viewModel.selectedDate, displayedComponents: .date)
                        .datePickerStyle(.graphical)
                }

                Section(header: Text("Planlanan Çalışma Blokları ve Etkinlikler")) {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack {
                            Rectangle()
                                .fill(Color.indigo)
                                .frame(width: 4)
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Çalışma Bloğu: YBS Vize Hazırlığı")
                                    .font(.headline)
                                Text("19:00 - 21:00 (120 dk)")
                                    .font(.subheadline)
                                    .foregroundColor(.secondary)
                                Text("Kriter: Akşam odaklanma tercihi (Hafıza tabanlı)")
                                    .font(.caption2)
                                    .foregroundColor(.indigo)
                            }
                            Spacer()
                            Image(systemName: "checkmark.seal.fill")
                                .foregroundColor(.green)
                        }
                        .padding(.vertical, 4)

                        Divider()

                        HStack {
                            Rectangle()
                                .fill(Color.orange)
                                .frame(width: 4)
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Hatırlatıcı: Ders Kaydı Onayı")
                                    .font(.headline)
                                Text("Son Tarih: 23:59")
                                    .font(.subheadline)
                                    .foregroundColor(.secondary)
                            }
                            Spacer()
                            Image(systemName: "bell.fill")
                                .foregroundColor(.orange)
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
            .navigationTitle("Takvim & Plan")
            .refreshable {
                await viewModel.loadCalendars()
            }
        }
    }
}
