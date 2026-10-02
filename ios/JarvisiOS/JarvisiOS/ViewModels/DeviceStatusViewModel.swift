import Foundation
import SwiftUI

@MainActor
public final class DeviceStatusViewModel: ObservableObject {
    @Published public var isConnected: Bool = false
    @Published public var modelName: String = "mlx-community/Qwen3.5-4B-MLX-4bit"
    @Published public var hostInfo: String = "Mac mini M4 (Local AI Server)"
    @Published public var latencyMs: Int = 42
    @Published public var lastHeartbeat: Date = Date()
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
        do {
            let res = try await apiService.checkHealth()
            isConnected = (res["status"] as? String) == "healthy"
            lastHeartbeat = Date()
        } catch {
            isConnected = false
            errorMessage = error.localizedDescription
        }
        isChecking = false
    }
}
