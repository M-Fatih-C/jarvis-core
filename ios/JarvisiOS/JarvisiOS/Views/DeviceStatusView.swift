import SwiftUI

public struct DeviceStatusView: View {
    @StateObject private var viewModel = DeviceStatusViewModel()

    public init() {}

    public var body: some View {
        NavigationStack {
            List {
                Section(header: Text("Yerel Yapay Zeka Sunucusu")) {
                    HStack {
                        Image(systemName: "macmini.fill")
                            .font(.system(size: 32))
                            .foregroundColor(.blue)

                        VStack(alignment: .leading, spacing: 4) {
                            Text(viewModel.hostInfo)
                                .font(.headline)
                            HStack {
                                Circle()
                                    .fill(viewModel.isConnected ? Color.green : Color.red)
                                    .frame(width: 8, height: 8)
                                Text(viewModel.isConnected ? "Çevrimiçi (Aktif)" : "Bağlantı Kesildi")
                                    .font(.caption)
                                    .foregroundColor(viewModel.isConnected ? .green : .red)
                            }
                        }
                        Spacer()
                    }
                    .padding(.vertical, 4)
                }

                Section(header: Text("Model & Çalışma Motoru")) {
                    HStack {
                        Text("Çalışan Model")
                        Spacer()
                        Text(viewModel.modelName)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }

                    HStack {
                        Text("Yürütme Mimarisi")
                        Spacer()
                        Text("Apple Silicon MLX 4-bit")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }

                    HStack {
                        Text("Çalışma Modu")
                        Spacer()
                        Text(viewModel.activeMode)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }

                    HStack {
                        Text("Gecikme Süresi")
                        Spacer()
                        if let ms = viewModel.latencyMs {
                            Text("\(ms) ms")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        } else {
                            Text("Ölçülemedi")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                }

                Section(header: Text("Kanal & İletişim Güvenliği")) {
                    HStack {
                        Text("İstemci - Sunucu Kanalı")
                        Spacer()
                        Text("Firestore Command Queue + Local API")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }

                    HStack {
                        Text("Yerel IPC Köprüsü")
                        Spacer()
                        Text("Unix Domain Socket (0600)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }

                    HStack {
                        Text("Güvenlik & Politika")
                        Spacer()
                        Text("PolicyEngine R2 Human-in-the-Loop")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }

                Section {
                    Button(action: {
                        Task {
                            await viewModel.checkStatus()
                        }
                    }) {
                        if viewModel.isChecking {
                            HStack {
                                Spacer()
                                ProgressView()
                                Spacer()
                            }
                        } else {
                            HStack {
                                Spacer()
                                Label("Bağlantıyı Yeniden Kontrol Et", systemImage: "arrow.clockwise")
                                Spacer()
                            }
                        }
                    }
                }
            }
            .navigationTitle("Sunucu Durumu")
            .refreshable {
                await viewModel.checkStatus()
            }
        }
    }
}
