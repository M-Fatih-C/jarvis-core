import Foundation
import SwiftUI

public struct ChatMessageItem: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let sender: MessageSender
    public let text: String
    public let timestamp: Date

    public enum MessageSender: Sendable {
        case user
        case jarvis
    }

    public init(id: UUID = UUID(), sender: MessageSender, text: String, timestamp: Date = Date()) {
        self.id = id
        self.sender = sender
        self.text = text
        self.timestamp = timestamp
    }
}

@MainActor
public final class ChatViewModel: ObservableObject {
    @Published public var messages: [ChatMessageItem] = []
    @Published public var inputText: String = ""
    @Published public var isSending: Bool = false
    @Published public var errorMessage: String? = nil

    private let apiService = JarvisAPIService.shared

    public init() {
        // Welcome message
        messages.append(
            ChatMessageItem(
                sender: .jarvis,
                text: "Merhaba Fatih! Ben Jarvis. Mac mini M4 üzerindeki yerel yapay zeka çekirdeğimle hizmetinizdeyim. Bugün nasıl yardımcı olabilirim?"
            )
        )
    }

    public func sendMessage() {
        let text = inputText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        inputText = ""
        messages.append(ChatMessageItem(sender: .user, text: text))
        isSending = true
        errorMessage = nil

        Task {
            do {
                let response = try await apiService.sendChatMessage(prompt: text)
                messages.append(ChatMessageItem(sender: .jarvis, text: response))
            } catch {
                errorMessage = "Bağlantı hatası: \(error.localizedDescription)"
                messages.append(ChatMessageItem(sender: .jarvis, text: "⚠️ Mac mini M4 sunucusuyla iletişim kurulamadı. Lütfen ağ bağlantınızı ve Jarvis Core durumunu kontrol edin."))
            }
            isSending = false
        }
    }
}
