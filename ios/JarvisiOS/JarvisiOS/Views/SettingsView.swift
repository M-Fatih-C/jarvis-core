import SwiftUI

public struct SettingsView: View {
    @EnvironmentObject private var viewModel: AuthViewModel
    @State private var emailInput: String = ""
    @State private var passwordInput: String = ""

    public init() {}

    public var body: some View {
        NavigationStack {
            Form {
                if viewModel.isAuthenticated {
                    Section(header: Text("Kullanıcı Hesabı (Firebase)")) {
                        HStack {
                            Image(systemName: "person.crop.circle.fill")
                                .font(.system(size: 36))
                                .foregroundColor(.blue)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(viewModel.userEmail.isEmpty ? "Giriş Yapıldı" : viewModel.userEmail)
                                    .font(.headline)
                                Text("UID: \(viewModel.userId)")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .padding(.vertical, 4)

                        HStack {
                            Text("Firebase Projesi")
                            Spacer()
                            Text(viewModel.projectId)
                                .font(.caption)
                                .foregroundColor(.secondary)
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
                            Text("Geçerlilik Mac üzerinden doğrulanır")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    Section("Araba ve ses") {
                        NavigationLink { CarPlaySetupView() } label: { Label("CarPlay karşılama", systemImage: "car.fill") }
                    }

                    Section(header: Text("Hakkında")) {
                        HStack {
                            Text("Uygulama Sürümü")
                            Spacer()
                            Text("Jarvis iOS 1.3")
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
                } else {
                    Section(header: Text("Giriş Yap (Firebase)")) {
                        TextField("E-posta adresi", text: $emailInput)
                            .keyboardType(.emailAddress)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)

                        SecureField("Parola", text: $passwordInput)

                        TextField("Firebase Proje ID", text: $viewModel.projectId)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)

                        SecureField("Firebase API Anahtarı (Opsiyonel)", text: $viewModel.apiKey)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)

                        if let err = viewModel.errorMessage {
                            Text(err)
                                .font(.caption)
                                .foregroundColor(.red)
                        }

                        Button(action: {
                            Task {
                                _ = await viewModel.signIn(email: emailInput, pass: passwordInput)
                            }
                        }) {
                            HStack {
                                Spacer()
                                if viewModel.isLoading {
                                    ProgressView()
                                } else {
                                    Text("Giriş Yap")
                                }
                                Spacer()
                            }
                        }
                        .disabled(viewModel.isLoading || emailInput.isEmpty || passwordInput.isEmpty)
                        Button("Yeni JARVIS hesabı oluştur") {
                            Task {
                                if await viewModel.signIn(email: emailInput, pass: passwordInput, createAccount: true) { passwordInput = "" }
                            }
                        }
                        .disabled(viewModel.isLoading || emailInput.isEmpty || passwordInput.count < 6)

                    }
                }
            }
            .navigationTitle("Ayarlar")
        }
    }
}


private struct CarPlaySetupView: View {
    @State private var preview = ""
    @State private var loading = false
    var body: some View {
        List {
            Section {
                Text("Arabaya bağlandığında ‘Merhaba patron’ ile başlayan tarih, hava durumu, kalan etkinlikler ve bağlantı özeti.")
                Text("Bir kez iPhone Kestirmeler uygulamasında kurulur. Mac açık ve internete bağlı olmalı; telefonun aynı Wi-Fi’da olması gerekmez.")
                    .foregroundStyle(.secondary)
            }
            Section("1 · Karşılama kestirmesi") {
                Text("Kestirmeler’de yeni kestirme oluştur; adını ‘JARVIS Karşılama’ yap.")
                Text("‘Şu Anki Hava Durumunu Al’ eylemini ekle; konumu seç. Sonra bir Metin eyleminde hava durumu Koşullar ve Sıcaklık değişkenlerini birleştir.")
                Text("JARVIS → ‘Yolculuk özetini al’ eylemini ekle. Hava durumu alanına bu Metin sonucunu bağla.")
                Text("Sonuna ‘Metni Seslendir’ ekle; okunacak metin olarak Yolculuk özeti sonucunu, dil olarak Türkçe’yi seç.")
            }
            Section("2 · CarPlay’e bağla") {
                Text("Otomasyon → + → CarPlay → Bağlandığında → Hemen Çalıştır. ‘JARVIS Karşılama’ kestirmesini seç.")
                Text("Kestirmeyi önce telefonunda bir kez çalıştır ve gereken izinleri tamamla. Araç bağlantısı testini park halindeyken yap.")
                Link("Kestirmeler’i aç", destination: URL(string: "shortcuts://")!)
            }
            Section {
                Button {
                    loading = true
                    Task { preview = await DailyBriefingService.build(); loading = false }
                } label: {
                    if loading { ProgressView("Takvim ve bağlantı kontrol ediliyor…") }
                    else { Text("Tarih, takvim ve bağlantıyı dene") }
                }.disabled(loading)
                if !preview.isEmpty { Text(preview).textSelection(.enabled) }
            } header: {
                Text("Önizleme")
            } footer: {
                Text("Bu önizlemede hava durumu yoktur; otomasyonda iPhone sağlar. Karşılama yalnızca bilgi okur. CarPlay otomasyonunu bu ekran kendiliğinden oluşturmaz.")
            }
        }.navigationTitle("CarPlay karşılama").navigationBarTitleDisplayMode(.inline)
    }
}
