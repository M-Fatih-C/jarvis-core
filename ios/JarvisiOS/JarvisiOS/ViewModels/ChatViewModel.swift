import Foundation
import SwiftUI

public struct PendingApprovalData: Equatable, Sendable {
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

public struct ChatMessageItem: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let sender: MessageSender
    public var text: String
    public let timestamp: Date
    public var status: MessageStatus
    public var commandId: String?
    public var approvalData: PendingApprovalData?

    public enum MessageSender: Sendable {
        case user
        case jarvis
    }

    public enum MessageStatus: String, Sendable {
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
    @Published public var messages: [ChatMessageItem] = []
    @Published public var inputText: String = ""
    @Published public var isSending: Bool = false
    @Published public var errorMessage: String? = nil
    @Published public var connectionState: String = "Bağlı"

    private let queueService = FirebaseCommandQueueService.shared
    private var inFlightCommandIds: Set<String> = []

    public init() {
        // Welcome message
        messages.append(
            ChatMessageItem(
                sender: .jarvis,
                text: "Merhaba! Ben Jarvis. Mac mini M4 üzerindeki yerel Qwen modelim ve Firebase Cloud Command Queue üzerinden hizmetinizdeyim. Bugün nasıl yardımcı olabilirim?"
            )
        )
    }

    /// Submit a message into the Cloud Command Queue and monitor execution states.
    public func sendMessage(userId: String = "user_fatih_01", projectId: String = "jarvis-local-dev") {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        inputText = ""
        isSending = true
        errorMessage = nil

        let userMsg = ChatMessageItem(sender: .user, text: text)
        messages.append(userMsg)

        let jarvisMsgId = UUID()
        let placeholderMsg = ChatMessageItem(
            id: jarvisMsgId,
            sender: .jarvis,
            text: "",
            status: .queued
        )
        messages.append(placeholderMsg)

        Task {
            do {
                // 1. Submit to user-scoped Firestore commands queue
                let commandId = try await queueService.submitChatCommand(
                    message: text,
                    userId: userId,
                    projectId: projectId
                )

                if let idx = self.messages.firstIndex(where: { $0.id == jarvisMsgId }) {
                    self.messages[idx].commandId = commandId
                }

                // 2. Poll for lifecycle status transitions
                let completedCmd = try await queueService.pollCommand(
                    commandId: commandId,
                    userId: userId,
                    projectId: projectId,
                    timeoutSeconds: 60.0
                ) { [weak self] status, resultData in
                    guard let self = self else { return }
                    if let idx = self.messages.firstIndex(where: { $0.id == jarvisMsgId }) {
                        switch status {
                        case .queued:
                            self.messages[idx].status = .queued
                        case .leased, .running:
                            self.messages[idx].status = .running
                        case .waitingApproval:
                            self.messages[idx].status = .waitingApproval
                            if let res = resultData {
                                let appID = (res["approval_id"] as? String) ?? ""
                                let tool = (res["pending_tool"] as? String) ?? "Eylem"
                                var argsMap: [String: String] = [:]
                                if let args = res["arguments"] as? [String: Any] {
                                    for (k, v) in args {
                                        argsMap[k] = "\(v)"
                                    }
                                }
                                let digest = res["action_digest"] as? String
                                self.messages[idx].approvalData = PendingApprovalData(
                                    approvalId: appID,
                                    pendingTool: tool,
                                    arguments: argsMap,
                                    actionDigest: digest
                                )
                                self.messages[idx].text = "⚠️ Bu işlem human-in-the-loop onayı gerektiriyor: \(tool)"
                            }
                        case .completed:
                            self.messages[idx].status = .completed
                        case .failed:
                            self.messages[idx].status = .failed
                        default:
                            break
                        }
                    }
                }

                // 3. Command Finalized
                if let idx = self.messages.firstIndex(where: { $0.id == jarvisMsgId }) {
                    if completedCmd.status == .completed {
                        if let resObj = completedCmd.result, let responseText = resObj["response"]?.value as? String {
                            self.messages[idx].text = responseText
                            self.messages[idx].status = .completed
                        } else if let dataObj = completedCmd.result?["data"]?.value {
                            self.messages[idx].text = "İşlem sonucu: \(dataObj)"
                            self.messages[idx].status = .completed
                        } else {
                            self.messages[idx].text = "Komut başarıyla tamamlandı ancak beklenen yanıt formatı çözümlenemedi."
                            self.messages[idx].status = .failed
                        }
                    } else if completedCmd.status == .waitingApproval {
                        // Already handled in status callback
                    } else {
                        self.messages[idx].text = completedCmd.error ?? "Komut yürütme başarısız oldu."
                        self.messages[idx].status = .failed
                    }
                }

            } catch {
                if let idx = self.messages.firstIndex(where: { $0.id == jarvisMsgId }) {
                    self.messages[idx].status = .failed
                    self.messages[idx].text = "⚠️ Hata: \(error.localizedDescription)"
                }
                self.errorMessage = error.localizedDescription
                self.connectionState = "Bağlantı Kesildi"
            }
            self.isSending = false
        }
    }

    /// Submit an approval response directly from a chat card.
    public func respondToApproval(
        messageId: UUID,
        approvalId: String,
        decision: String,
        actionDigest: String?,
        userId: String = "user_fatih_01",
        projectId: String = "jarvis-local-dev"
    ) {
        guard let idx = messages.firstIndex(where: { $0.id == messageId }) else { return }
        messages[idx].status = .running
        messages[idx].text = decision == "approved" ? "Onay iletildi, eylem yürütülüyor..." : "İşlem reddedildi."

        Task {
            do {
                let cmdId = try await queueService.submitApprovalResponse(
                    approvalId: approvalId,
                    decision: decision,
                    actionDigest: actionDigest,
                    userId: userId,
                    projectId: projectId
                )

                let finalCmd = try await queueService.pollCommand(
                    commandId: cmdId,
                    userId: userId,
                    projectId: projectId
                ) { _, _ in }

                if let res = finalCmd.result, let resp = res["response"]?.value as? String {
                    self.messages[idx].text = resp
                    self.messages[idx].status = .completed
                } else {
                    self.messages[idx].text = finalCmd.error ?? "İşlem sonuçlandı."
                    self.messages[idx].status = finalCmd.status == .completed ? .completed : .failed
                }
                self.messages[idx].approvalData = nil
            } catch {
                self.messages[idx].status = .failed
                self.messages[idx].text = "Onay gönderilemedi: \(error.localizedDescription)"
            }
        }
    }
}
