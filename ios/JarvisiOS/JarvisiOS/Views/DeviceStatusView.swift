import SwiftUI

public struct DeviceStatusView: View {
    @StateObject private var viewModel = DeviceStatusViewModel()
    public init() {}
    public var body: some View {
        List {
            Section {
                Label(viewModel.isConnected ? "Mac mini bağlı" : "Mac mini bağlantısı bekleniyor",
                      systemImage: viewModel.isConnected ? "checkmark.circle.fill" : "wifi.slash")
                    .foregroundStyle(viewModel.isConnected ? .green : .orange)
                if let date = viewModel.lastHeartbeat {
                    LabeledContent("Son haber", value: date.formatted(date: .omitted, time: .standard))
                }
                if let error = viewModel.errorMessage { Text(error).font(.caption).foregroundStyle(.orange) }
            }
            Section("Bağlantılar") {
                ForEach(viewModel.services.keys.sorted(), id: \.self) { name in
                    LabeledContent(name, value: viewModel.services[name] ?? "Bilinmiyor")
                }
                LabeledContent("Güvenli giriş", value: "Face ID / cihaz parolası")
                LabeledContent("Ses", value: "iPhone Türkçe sesi")
            }
            Section {
                Text("Mac mini açık ve internete bağlı olmalı. Geçici bağlantı kesintileri hesabını kapatmaz. Takvim ve hatırlatıcı değişiklikleri onayınla kaydedilir.")
                    .font(.footnote).foregroundStyle(.secondary)
                Button("Bağlantıyı yenile", systemImage: "arrow.clockwise") { Task { await viewModel.checkStatus() } }
                    .disabled(viewModel.isChecking)
            }
        }
        .navigationTitle("Bağlantılar")
        .overlay { if viewModel.isChecking && viewModel.lastHeartbeat == nil { ProgressView() } }
        .task {
            while !Task.isCancelled {
                await viewModel.checkStatus()
                do { try await Task.sleep(nanoseconds: 20_000_000_000) } catch { break }
            }
        }
        .refreshable { await viewModel.checkStatus() }
    }
}
