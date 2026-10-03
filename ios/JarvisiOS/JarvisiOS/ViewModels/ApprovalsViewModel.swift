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

    public init() {}

    public func loadData() async {
        guard !isLoading else { return }
        isLoading = true
        errorMessage = nil
        do {
            let details = try await apiService.fetchProposalDetails()
            proposals = details.map(\.task)
            detailedTasks = Dictionary(uniqueKeysWithValues: details.map { ($0.task.task_id, $0) })
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    public func approveAction(actionId: String, taskId: String) async {
        guard processingActionId == nil,
              let action = detailedTasks[taskId]?.actions.first(where: { $0.id == actionId }),
              let approval = action.approval_id else { return }
        processingActionId = actionId
        let auth = FirebaseAuthService.shared
        let key = "jarvis_task_approval.\(auth.currentProjectID ?? "").\(auth.currentUserID ?? "").\(approval)"
        let commandId = UserDefaults.standard.string(forKey: key) ?? UUID().uuidString
        UserDefaults.standard.set(commandId, forKey: key)
        do {
            _ = try await apiService.approveAction(action, commandId: commandId)
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
