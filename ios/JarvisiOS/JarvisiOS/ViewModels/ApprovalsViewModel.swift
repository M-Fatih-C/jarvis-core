import Foundation
import SwiftUI

@MainActor
public final class ApprovalsViewModel: ObservableObject {
    @Published public var proposals: [TaskProposalDTO] = []
    @Published public var detailedTasks: [String: TaskProposalDetailDTO] = [:]
    @Published public var isLoading: Bool = false
    @Published public var errorMessage: String? = nil
    @Published public var processingActionId: String? = nil

    private let apiService = JarvisAPIService.shared

    public var pendingApprovalsCount: Int {
        var count = 0
        for (_, detail) in detailedTasks {
            count += detail.actions.filter { $0.status == .waitingApproval || $0.status == .proposed }.count
        }
        return count
    }

    public init() {
        Task {
            await loadData()
        }
    }

    public func loadData() async {
        isLoading = true
        errorMessage = nil
        do {
            let fetched = try await apiService.fetchProposals()
            proposals = fetched

            // Fetch details for each proposal to load planned actions
            for p in fetched {
                if let detail = try? await apiService.fetchProposalDetail(taskId: p.task_id) {
                    detailedTasks[p.task_id] = detail
                }
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    public func approveAction(actionId: String, taskId: String) async {
        processingActionId = actionId
        do {
            _ = try await apiService.approveAction(actionId: actionId)
            // Reload details
            if let detail = try? await apiService.fetchProposalDetail(taskId: taskId) {
                detailedTasks[taskId] = detail
            }
        } catch {
            errorMessage = "Onaylama başarısız: \(error.localizedDescription)"
        }
        processingActionId = nil
    }

    public func dismissAction(actionId: String, taskId: String) async {
        processingActionId = actionId
        do {
            _ = try await apiService.dismissAction(actionId: actionId)
            if let detail = try? await apiService.fetchProposalDetail(taskId: taskId) {
                detailedTasks[taskId] = detail
            }
        } catch {
            errorMessage = "Reddetme başarısız: \(error.localizedDescription)"
        }
        processingActionId = nil
    }

    public func planReminder(taskId: String, due: String? = nil) async {
        isLoading = true
        do {
            _ = try await apiService.planReminder(taskId: taskId, due: due)
            if let detail = try? await apiService.fetchProposalDetail(taskId: taskId) {
                detailedTasks[taskId] = detail
            }
        } catch {
            errorMessage = "Hatırlatıcı planlama hatası: \(error.localizedDescription)"
        }
        isLoading = false
    }

    public func planWorkBlock(taskId: String) async {
        isLoading = true
        do {
            _ = try await apiService.planWorkBlock(taskId: taskId, duration: 120)
            if let detail = try? await apiService.fetchProposalDetail(taskId: taskId) {
                detailedTasks[taskId] = detail
            }
        } catch {
            errorMessage = "Çalışma bloğu planlama hatası: \(error.localizedDescription)"
        }
        isLoading = false
    }

    public func requestApproval(actionId: String, taskId: String) async {
        processingActionId = actionId
        do {
            _ = try await apiService.requestApproval(actionId: actionId)
            if let detail = try? await apiService.fetchProposalDetail(taskId: taskId) {
                detailedTasks[taskId] = detail
            }
        } catch {
            errorMessage = "Onay talebi oluşturulamadı: \(error.localizedDescription)"
        }
        processingActionId = nil
    }
}
