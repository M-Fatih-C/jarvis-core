import SwiftUI

public struct SettingsView: View {
    @EnvironmentObject private var auth: AuthViewModel
    public init() {}
    public var body: some View {
        NavigationStack {
            Form {
                Section("Hesabın") {
                    Label(auth.userEmail.isEmpty ? "Oturum açık" : auth.userEmail, systemImage: "person.crop.circle")
                    Label("Face ID ve cihaz parolasıyla korunuyor", systemImage: "faceid")
                    Text("JARVIS arka plana geçtiğinde oturumun kilitlenir.").font(.caption).foregroundStyle(.secondary)
                }
                Section("Bağlantılar") {
                    NavigationLink { DeviceStatusView() } label: { Label("Bağlantılar ve sistem durumu", systemImage: "network") }
                    NavigationLink { CarPlaySetupView() } label: { Label("CarPlay karşılama", systemImage: "car.fill") }
                    Label("Ses: iPhone’un Türkçe sesi", systemImage: "speaker.wave.2")
                    Text("Ücretli ses sağlayıcısı bağlı değil.").font(.caption).foregroundStyle(.secondary)
                }
                Section("Uygulama") {
                    HStack {
                        Text("Sürüm"); Spacer()
                        Text("\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "") (\(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? ""))")
                            .foregroundStyle(.secondary)
                    }
                }
                Section {
                    Button("Oturumu kilitle") { auth.enteredBackground() }
                    Button("Oturumu kapat", role: .destructive) { auth.signOut() }
                }
            }.navigationTitle("Ayarlar")
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
                Text("Face ID kilidi açık değilse otomatik karşılama yalnızca tarih ve verilen hava durumunu okur. Özel takvim özeti için JARVIS’i açıp kimliğini doğrula; ardından bu ekrandaki önizlemeyi kullan.").foregroundStyle(.orange)
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
