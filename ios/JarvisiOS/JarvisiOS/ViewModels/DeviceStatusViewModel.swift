import Foundation
import SwiftUI

@MainActor
public final class DeviceStatusViewModel: ObservableObject {
    @Published public var isConnected: Bool = false
    @Published public var modelName: String = "mlx-community/Qwen3.5-4B-MLX-4bit"
    @Published public var hostInfo: String = "Mac mini M4 (Local AI Server)"
    @Published public var latencyMs: Int? = nil
    @Published public var lastHeartbeat: Date? = nil
    @Published public var activeMode: String = "Assist Mode"
    @Published public var isChecking: Bool = false
    @Published public var errorMessage: String? = nil

    @Published var services: [String: String] = [:]
    private let apiService = JarvisAPIService.shared

    public init() {}

    public func checkStatus() async {
        guard !isChecking else { return }
        isChecking = true
        errorMessage = nil
        let start = Date()
        do {
            let res = try await apiService.checkHealth()
            let elapsed = Int(Date().timeIntervalSince(start) * 1000)
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            let timestamp = res["last_seen_at"] as? String ?? ""
            lastHeartbeat = formatter.date(from: timestamp) ?? ISO8601DateFormatter().date(from: timestamp)
            let age = lastHeartbeat.map { Date().timeIntervalSince($0) } ?? .infinity
            isConnected = (res["status"] as? String) == "online" && age >= -60 && age < 100
            if let health = res["health"] as? [String: Any] {
                let fresh = isConnected
                func state(_ flag: String) -> String { fresh ? (health[flag] as? Bool == true ? "Hazır" : "Kontrol gerekli") : "Ulaşılamıyor" }
                func permission(_ name: String) -> String {
                    guard fresh else { return "Ulaşılamıyor" }
                    return ["authorized", "full_access"].contains(health[name] as? String ?? "") ? "Bağlı" : "İzin kontrol edilmeli"
                }
                services = ["Yerel yapay zekâ": state("model_ready"), "Komut sistemi": state("worker_running"),
                    "Takvim": permission("calendar_permission"), "Hatırlatıcılar": permission("reminders_permission"),
                    "Gmail yetkisi": state("gmail_authorized"), "Yeni e-posta takibi": state("gmail_new_only"),
                    "09.00 / 20.00 kontrolü": state("gmail_schedule_enabled"), "Otomatik imza yenileme": state("signature_auto_renew")]
                if let date = health["signature_expires"] as? String, let expiration = CalendarDates.parse(date) {
                    services["Uygulama imzası"] = expiration.formatted(date: .abbreviated, time: .omitted)
                }
                modelName = (health["model"] as? String) ?? "Bilinmiyor"
                if health["model_ready"] as? Bool != true { errorMessage = "Yerel model hazır değil." }
                if health["mac_agent_connected"] as? Bool != true { errorMessage = "MacAgent bağlantısı yok." }
            }
            if age >= 100 { errorMessage = "Mac sinyali güncel değil; uyku veya ağ bağlantısını kontrol edin." }
            latencyMs = isConnected ? elapsed : nil

        } catch {
            isConnected = false
            services = [:]
            latencyMs = nil
            errorMessage = error.localizedDescription
        }
        isChecking = false
    }
}
