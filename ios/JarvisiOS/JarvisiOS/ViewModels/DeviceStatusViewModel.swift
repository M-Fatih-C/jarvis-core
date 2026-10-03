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

    private let apiService = JarvisAPIService.shared

    public init() {
        Task {
            await checkStatus()
        }
    }

    public func checkStatus() async {
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
                modelName = (health["model"] as? String) ?? "Bilinmiyor"
                if health["model_ready"] as? Bool != true { errorMessage = "Yerel model hazır değil." }
                if health["mac_agent_connected"] as? Bool != true { errorMessage = "MacAgent bağlantısı yok." }
            }
            if age >= 100 { errorMessage = "Mac sinyali güncel değil; uyku veya ağ bağlantısını kontrol edin." }
            latencyMs = isConnected ? elapsed : nil

        } catch {
            isConnected = false
            latencyMs = nil
            errorMessage = error.localizedDescription
        }
        isChecking = false
    }
}
