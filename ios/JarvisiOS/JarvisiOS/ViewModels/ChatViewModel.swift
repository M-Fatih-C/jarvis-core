import Foundation
import SwiftUI

public struct PendingApprovalData: Equatable, Codable, Sendable {
    public let approvalId: String
    public let pendingTool: String
    public let arguments: [String: String]
    public let actionDigest: String?

    public init(approvalId: String, pendingTool: String, arguments: [String: String], actionDigest: String? = nil) {
        self.approvalId = approvalId
        self.pendingTool = pendingTool
        self.arguments = arguments
        self.actionDigest = actionDigest
    }
}

public struct ChatMessageItem: Identifiable, Equatable, Codable, Sendable {
    public let id: UUID
    public let sender: MessageSender
    public var text: String
    public let timestamp: Date
    public var status: MessageStatus
    public var commandId: String?
    public var approvalData: PendingApprovalData?

    public enum MessageSender: String, Codable, Sendable {
        case user
        case jarvis
    }

    public enum MessageStatus: String, Codable, Sendable {
        case queued = "Sırada bekliyor..."
        case running = "Jarvis düşünüyor (Qwen M4)..."
        case waitingApproval = "İşlem onayı bekleniyor"
        case completed = "Tamamlandı"
        case failed = "Başarısız"
    }

    public init(
        id: UUID = UUID(),
        sender: MessageSender,
        text: String,
        timestamp: Date = Date(),
        status: MessageStatus = .completed,
        commandId: String? = nil,
        approvalData: PendingApprovalData? = nil
    ) {
        self.id = id
        self.sender = sender
        self.text = text
        self.timestamp = timestamp
        self.status = status
        self.commandId = commandId
        self.approvalData = approvalData
    }
}

@MainActor
public final class ChatViewModel: ObservableObject {
    @Published public var messages: [ChatMessageItem] = [] { didSet { persistHistory() } }
    @Published public var inputText = ""
    @Published public var isSending = false
    @Published public private(set) var isRestoring = false
    @Published public var errorMessage: String?
    @Published public var connectionState = "Bağlantı bekleniyor"
    private let queueService = FirebaseCommandQueueService.shared
    var onResponse: ((String) -> Void)?
    var onFailure: ((String) -> Void)?
    private var inFlightCommandIds: Set<String> = []
    private var activeAccount = ""
    private let history = ChatHistoryStore()
    private var historyLoaded = false
    private var needsCloudHistory = true
    private let sessionIdentifier = FirebaseAuthService.shared.sessionIdentifier

    public init() {
        messages = [ChatMessageItem(sender: .jarvis, text: "Merhaba! Bugün nasıl yardımcı olabilirim?")]
    }

    private func persistHistory() {
        let auth = FirebaseAuthService.shared
        guard historyLoaded, auth.sessionIdentifier == sessionIdentifier, let uid = auth.currentUserID, let project = auth.currentProjectID,
              activeAccount == "\(project).\(uid)", let key = auth.historyKey else { return }
        do { try history.save(messages, account: activeAccount, key: key) }
        catch { errorMessage = "Sohbet bu cihaza kaydedilemedi. Firebase’deki iletiler korunuyor." }
    }

    private func conversationId(userId: String, projectId: String) -> String {
        let key = "jarvis_conversation.\(projectId).\(userId)"
        if let id = UserDefaults.standard.string(forKey: key) { return id }
        let id = UUID().uuidString
        UserDefaults.standard.set(id, forKey: key)
        return id
    }

    public func sendMessage(userId: String, projectId: String) {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !isSending, !isRestoring, !userId.isEmpty else { return }
        inputText = ""
        isSending = true
        errorMessage = nil
        let commandId = UUID().uuidString
        messages.append(ChatMessageItem(sender: .user, text: text))
        let message = ChatMessageItem(sender: .jarvis, text: "", status: .queued, commandId: commandId)
        messages.append(message)
        inFlightCommandIds.insert(commandId)
        Task {
            defer { inFlightCommandIds.remove(commandId); isSending = false }
            do {
                _ = try await queueService.submitChatCommand(
                    message: text, conversationId: conversationId(userId: userId, projectId: projectId),
                    userId: userId, projectId: projectId,
                    existingCommandId: commandId, existingIdempotencyKey: commandId
                )
                try await monitor(commandId: commandId, messageId: message.id, userId: userId, projectId: projectId)
            } catch { showError(error, messageId: message.id) }
        }
    }

    private func apply(status: CommandStatusDTO, result: [String: Any]?, messageId: UUID) {
        guard let idx = messages.firstIndex(where: { $0.id == messageId }) else { return }
        switch status {
        case .queued: messages[idx].status = .queued
        case .leased, .running: messages[idx].status = .running
        case .waitingApproval:
            messages[idx].status = .waitingApproval
            guard let result, let id = result["approval_id"] as? String,
                  let digest = result["action_digest"] as? String, !id.isEmpty, !digest.isEmpty else {
                messages[idx].status = .failed
                messages[idx].text = "Onay ayrıntıları doğrulanamadı."
                return
            }
            let tool = result["pending_tool"] as? String ?? "Eylem"
            let args = (result["arguments"] as? [String: Any] ?? [:]).mapValues { String(describing: $0) }
            messages[idx].approvalData = PendingApprovalData(approvalId: id, pendingTool: tool, arguments: args, actionDigest: digest)
            messages[idx].text = "Bu işlem onayınızı gerektiriyor: \(tool)"
        case .completed:
            messages[idx].approvalData = nil
            if let response = result?["response"] as? String {
                messages[idx].text = response
                messages[idx].status = .completed
            } else if let data = result?["data"] {
                messages[idx].text = "İşlem sonucu: \(data)"
                messages[idx].status = .completed
            } else {
                messages[idx].text = "Sonuç ayrıntıları alınamadı. İşlemi tekrarlamadan önce kontrol edin."
                messages[idx].status = .failed
            }
        case .failed, .cancelled, .expired:
            messages[idx].status = .failed
            messages[idx].approvalData = nil
        }
    }

    private func monitor(commandId: String, messageId: UUID, userId: String, projectId: String) async throws {
        let final = try await queueService.pollCommand(commandId: commandId, userId: userId, projectId: projectId) { [weak self] status, result in
            self?.apply(status: status, result: result, messageId: messageId)
        }
        connectionState = "Bağlı"
        if final.status == .completed, let text = final.result?["response"]?.value as? String { onResponse?(text) }
    }

    private func showError(_ error: Error, messageId: UUID) {
        if case AuthError.locked = error { return }
        if error is CancellationError { return }
        guard let idx = messages.firstIndex(where: { $0.id == messageId }) else { return }
        messages[idx].status = .failed
        messages[idx].text = "Sonuç alınamadı: \(error.localizedDescription) Uygulama tekrar açıldığında aynı isteğin durumu kontrol edilir."
        errorMessage = error.localizedDescription
        connectionState = "Bağlantı kontrol edilmeli"
        onFailure?(error.localizedDescription)
    }

    private func restoreHistory(userId: String, projectId: String) async {
        guard !isRestoring else { return }
        isRestoring = true
        defer { isRestoring = false }
        let account = "\(projectId).\(userId)"
        if activeAccount != account {
            historyLoaded = false
            activeAccount = account
            if let key = FirebaseAuthService.shared.historyKey {
                do { messages = try history.load(account: account, key: key) }
                catch { messages = [] }
            }
            needsCloudHistory = messages.isEmpty
        }
        if needsCloudHistory {
            do {
                let commands = try await queueService.recentChatCommands(userId: userId, projectId: projectId,
                    conversationId: conversationId(userId: userId, projectId: projectId))
                guard FirebaseAuthService.shared.sessionIdentifier == sessionIdentifier else { return }
                let knownIds = Set(messages.compactMap(\.commandId))
                for cmd in commands where !knownIds.contains(cmd.id) {
                    let date = CalendarDates.parse(cmd.created_at) ?? Date()
                    if let input = cmd.payload["input"]?.value as? String {
                        messages.append(ChatMessageItem(sender: .user, text: input, timestamp: date))
                    }
                    let reply = ChatMessageItem(sender: .jarvis, text: "", timestamp: date, status: .queued, commandId: cmd.id)
                    messages.append(reply)
                    apply(status: cmd.status, result: cmd.result?.mapValues { $0.value }, messageId: reply.id)
                    if cmd.status == .failed { showError(CommandQueueError.commandFailed(cmd.error ?? "İstek tamamlanamadı."), messageId: reply.id) }
                }
                messages.sort { $0.timestamp < $1.timestamp }
                needsCloudHistory = false
            } catch { errorMessage = "Geçmiş şu anda alınamıyor. Bağlantı geldiğinde tekrar dene." }
        }
        historyLoaded = true
        persistHistory()
    }

    /// Resume reads after restart/foreground; never resubmit an uncertain write.
    public func resumePending(userId: String, projectId: String) async {
        guard !userId.isEmpty, !projectId.isEmpty else { return }
        await restoreHistory(userId: userId, projectId: projectId)
        for id in queueService.getPendingCommandIds(userId: userId, projectId: projectId) {
            if inFlightCommandIds.contains(id) { continue }
            inFlightCommandIds.insert(id)
            defer { inFlightCommandIds.remove(id) }
            do {
                guard let existing = try await queueService.fetchCommandDocument(commandId: id, userId: userId, projectId: projectId) else {
                    queueService.forgetPendingCommand(id, userId: userId, projectId: projectId)
                    continue
                }
                let messageId: UUID
                if let message = messages.first(where: { $0.commandId == id }) { messageId = message.id }
                else {
                    let message = ChatMessageItem(sender: .jarvis, text: "Önceki isteğin sonucu kontrol ediliyor…", status: .queued, commandId: id)
                    messages.append(message)
                    messageId = message.id
                    if let text = existing.payload["input"]?.value as? String {
                        messages.insert(ChatMessageItem(sender: .user, text: text), at: max(0, messages.count - 1))
                    }
                }
                try await monitor(commandId: id, messageId: messageId, userId: userId, projectId: projectId)
            } catch {
                connectionState = "Bağlantı kontrol edilmeli"
            }
        }
    }

    public func respondToApproval(messageId: UUID, approvalId: String, decision: String, actionDigest: String?, userId: String, projectId: String) {
        guard let idx = messages.firstIndex(where: { $0.id == messageId }),
              messages[idx].status == .waitingApproval, let actionDigest, !userId.isEmpty else { return }
        let originalId = messages[idx].commandId
        let commandId = UUID().uuidString
        messages[idx].approvalData = nil
        messages[idx].status = .queued
        messages[idx].commandId = commandId
        messages[idx].text = "Karar iletiliyor…"
        inFlightCommandIds.insert(commandId)
        Task {
            defer { inFlightCommandIds.remove(commandId) }
            do {
                _ = try await queueService.submitApprovalResponse(approvalId: approvalId, decision: decision,
                    actionDigest: actionDigest, userId: userId, projectId: projectId,
                    existingCommandId: commandId, existingIdempotencyKey: commandId)
                if let originalId { queueService.forgetPendingCommand(originalId, userId: userId, projectId: projectId) }
                try await monitor(commandId: commandId, messageId: messageId, userId: userId, projectId: projectId)
            } catch { showError(error, messageId: messageId) }
        }
    }
}
