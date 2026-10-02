import SwiftUI

public struct SettingsView: View {
    @StateObject private var viewModel = AuthViewModel()

    public init() {}

    public var body: some View {
        NavigationStack {
            Form {
                Section(header: Text("Kullanıcı Hesabı")) {
                    HStack {
                        Image(systemName: "person.crop.circle.fill")
                            .font(.system(size: 36))
                            .foregroundColor(.blue)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Muhammed Fatih Çetintaş")
                                .font(.headline)
                            Text(viewModel.userEmail)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .padding(.vertical, 4)

                    HStack {
                        Text("Kullanıcı ID")
                        Spacer()
                        Text(viewModel.userId)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }

                Section(header: Text("Bağlantı Ayarları")) {
                    TextField("Sunucu Adresi", text: $viewModel.serverURL)
                        .autocapitalization(.none)
                        .disableAutocorrection(true)

                    Button("Ayarları Kaydet") {
                        viewModel.saveSettings()
                    }

                    if viewModel.isSaved {
                        Text("Ayarlar başarıyla kaydedildi ✅")
                            .font(.caption)
                            .foregroundColor(.green)
                    }
                }

                Section(header: Text("Apple Geliştirici & İmza Durumu")) {
                    HStack {
                        Text("Ekip Türü")
                        Spacer()
                        Text("Apple Personal Team")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }

                    HStack {
                        Text("İmza Geçerlilik Süresi")
                        Spacer()
                        Text("7 Gün (Otomatik Yenilemeli)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }

                Section(header: Text("Hakkında")) {
                    HStack {
                        Text("Uygulama Sürümü")
                        Spacer()
                        Text("Jarvis iOS 1.0 (Milestone 5)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }

                    HStack {
                        Text("Platform")
                        Spacer()
                        Text("Native SwiftUI / iOS 17+")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }

                Section {
                    Button(role: .destructive, action: {
                        viewModel.signOut()
                    }) {
                        HStack {
                            Spacer()
                            Text("Oturumu Kapat")
                            Spacer()
                        }
                    }
                }
            }
            .navigationTitle("Ayarlar")
        }
    }
}
