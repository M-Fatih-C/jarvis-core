import Foundation

public enum CommandQueueError: LocalizedError {
    case unauthenticated
    case commandTimeout
    case commandFailed(String)
    case networkError(String)
    case invalidResponse

    public var errorDescription: String? {
        switch self {
        case .unauthenticated:
            return "Oturum açık değil. Lütfen önce giriş yapın."
        case .commandTimeout:
            return "İstek zaman aşımına uğradı. Sunucu yanıt vermedi."
        case .commandFailed(let msg):
            return "Komut başarısız: \(msg)"
        case .networkError(let msg):
            return "Ağ hatası: \(msg)"
        case .invalidResponse:
            return "Sunucu yanıt şeması çözümlenemedi."
        }
    }
}

/// Dispatches user commands and approvals through authenticated Firestore REST API.
/// Fail-closed security:
/// - Unauthenticated requests are rejected.
/// - Network errors fail closed immediately and are never disguised as success.
/// - Offline commands are never represented as completed operations.
/// - Command identifiers and idempotency keys are preserved for safe retries.
public final class FirebaseCommandQueueService {
    public static let shared = FirebaseCommandQueueService()
    private let session = URLSession.shared
    private let pendingCommandsKey = "jarvis_pending_command_ids"

    private init() {}

    /// Base Firestore REST URL for a document.
    private func firestoreDocumentURL(projectId: String, path: String) -> URL? {
        let cleanProject = projectId.isEmpty ? "jarvis-local-dev" : projectId
        let str = "https://firestore.googleapis.com/v1/projects/\(cleanProject)/databases/(default)/documents/\(path)"
        return URL(string: str)
    }

    /// Retrieve the current authenticated ID token from Keychain.
    private func getAuthToken() throws -> String {
        guard let token = KeychainHelper.shared.read(key: "firebase_id_token"),
              !token.trimmingCharacters(in: .whitespaces).isEmpty else {
            throw CommandQueueError.unauthenticated
        }
        return token.trimmingCharacters(in: .whitespaces)
    }

    /// Submit a new user chat message into the Firestore Command Queue.
    public func submitChatCommand(
        message: String,
        userId: String,
        projectId: String,
        existingCommandId: String? = nil,
        existingIdempotencyKey: String? = nil
    ) async throws -> String {
        let commandId = existingCommandId ?? UUID().uuidString
        let idempotencyKey = existingIdempotencyKey ?? UUID().uuidString
        let nowStr = ISO8601DateFormatter().string(from: Date())

        let commandPayload: [String: Any] = [
            "id": commandId,
            "type": "agent_run",
            "name": "chat",
            "source_device": "iphone_13",
            "target_device": "mac-mini-main",
            "payload": [
                "input": message,
                "user_id": userId
            ],
            "status": "PENDING",
            "idempotency_key": idempotencyKey,
            "created_at": nowStr,
            "available_at": nowStr
        ]

        // Persist identifier before transmission for retry safety
        rememberPendingCommand(commandId)

        try await writeCommandDocument(commandId: commandId, userId: userId, projectId: projectId, payload: commandPayload)
        return commandId
    }

    /// Submit an approval response from iPhone to the Command Queue.
    public func submitApprovalResponse(
        approvalId: String,
        decision: String,
        actionDigest: String?,
        userId: String,
        projectId: String,
        existingCommandId: String? = nil,
        existingIdempotencyKey: String? = nil
    ) async throws -> String {
        let commandId = existingCommandId ?? UUID().uuidString
        let idempotencyKey = existingIdempotencyKey ?? UUID().uuidString
        let nowStr = ISO8601DateFormatter().string(from: Date())

        var payloadData: [String: Any] = [
            "approval_id": approvalId,
            "decision": decision,
            "user_id": userId
        ]
        if let digest = actionDigest {
            payloadData["action_digest"] = digest
        }

        let commandPayload: [String: Any] = [
            "id": commandId,
            "type": "approval_response",
            "name": "approval_decision",
            "source_device": "iphone_13",
            "target_device": "mac-mini-main",
            "payload": payloadData,
            "status": "PENDING",
            "idempotency_key": idempotencyKey,
            "created_at": nowStr,
            "available_at": nowStr
        ]

        // Persist identifier before transmission for retry safety
        rememberPendingCommand(commandId)

        try await writeCommandDocument(commandId: commandId, userId: userId, projectId: projectId, payload: commandPayload)
        return commandId
    }

    /// Poll command status until completed, waiting for approval, failed, or timed out.
    public func pollCommand(
        commandId: String,
        userId: String,
        projectId: String,
        timeoutSeconds: Double = 60.0,
        onStatusChange: @escaping (CommandStatusDTO, [String: Any]?) -> Void
    ) async throws -> CloudCommandDTO {
        let start = Date()
        var lastStatus: CommandStatusDTO? = nil

        while Date().timeIntervalSince(start) < timeoutSeconds {
            if let cmd = try await fetchCommandDocument(commandId: commandId, userId: userId, projectId: projectId) {
                if cmd.status != lastStatus {
                    lastStatus = cmd.status
                    onStatusChange(cmd.status, cmd.result)
                }

                if cmd.status == .completed {
                    forgetPendingCommand(commandId)
                    return cmd
                } else if cmd.status == .waitingApproval {
                    return cmd
                } else if cmd.status == .failed {
                    forgetPendingCommand(commandId)
                    throw CommandQueueError.commandFailed(cmd.error ?? "Bilinmeyen hata oluştu.")
                }
            }

            try await Task.sleep(nanoseconds: 1_000_000_000) // Poll every 1 second
        }

        throw CommandQueueError.commandTimeout
    }

    // --- Private Document Helpers (Authenticated Firestore REST API) ---

    private func writeCommandDocument(
        commandId: String,
        userId: String,
        projectId: String,
        payload: [String: Any]
    ) async throws {
        let authToken = try getAuthToken()

        guard let url = firestoreDocumentURL(projectId: projectId, path: "users/\(userId)/commands?documentId=\(commandId)") else {
            throw CommandQueueError.networkError("Geçersiz Firestore URL'si")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization")

        let firestoreFields = encodeToFirestoreFields(payload)
        let body: [String: Any] = ["fields": firestoreFields]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            // Fail closed: Network error is reported immediately and never disguised as success
            throw CommandQueueError.networkError("Firestore bağlantı hatası: \(error.localizedDescription)")
        }

        guard let httpRes = response as? HTTPURLResponse else {
            throw CommandQueueError.networkError("Sunucudan geçerli bir HTTP yanıtı alınamadı.")
        }

        guard httpRes.statusCode == 200 || httpRes.statusCode == 201 else {
            let errorBody = String(data: data, encoding: .utf8) ?? "Bilinmeyen hata"
            throw CommandQueueError.networkError("Firestore komut yazma reddedildi (HTTP \(httpRes.statusCode)): \(errorBody)")
        }
    }

    private func fetchCommandDocument(
        commandId: String,
        userId: String,
        projectId: String
    ) async throws -> CloudCommandDTO? {
        let authToken = try getAuthToken()

        guard let url = firestoreDocumentURL(projectId: projectId, path: "users/\(userId)/commands/\(commandId)") else {
            throw CommandQueueError.networkError("Geçersiz Firestore URL'si")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization")

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw CommandQueueError.networkError("Firestore okuma bağlantı hatası: \(error.localizedDescription)")
        }

        guard let httpRes = response as? HTTPURLResponse else {
            throw CommandQueueError.networkError("Sunucudan geçerli bir HTTP yanıtı alınamadı.")
        }

        if httpRes.statusCode == 404 {
            return nil
        }

        guard httpRes.statusCode == 200 else {
            throw CommandQueueError.networkError("Firestore komut okuma başarısız (HTTP \(httpRes.statusCode))")
        }

        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let fields = json["fields"] as? [String: Any] else {
            throw CommandQueueError.invalidResponse
        }

        let decoded = decodeFromFirestoreFields(fields)
        let jsonData = try JSONSerialization.data(withJSONObject: decoded)
        return try JSONDecoder().decode(CloudCommandDTO.self, from: jsonData)
    }

    // --- Idempotency & Safe Retry Tracking ---

    private func rememberPendingCommand(_ id: String) {
        var list = UserDefaults.standard.stringArray(forKey: pendingCommandsKey) ?? []
        if !list.contains(id) {
            list.append(id)
            UserDefaults.standard.set(list, forKey: pendingCommandsKey)
        }
    }

    private func forgetPendingCommand(_ id: String) {
        var list = UserDefaults.standard.stringArray(forKey: pendingCommandsKey) ?? []
        list.removeAll(where: { $0 == id })
        UserDefaults.standard.set(list, forKey: pendingCommandsKey)
    }

    public func getPendingCommandIds() -> [String] {
        return UserDefaults.standard.stringArray(forKey: pendingCommandsKey) ?? []
    }

    // --- Firestore Field Serialization ---

    private func encodeToFirestoreFields(_ dict: [String: Any]) -> [String: Any] {
        var fields: [String: Any] = [:]
        for (key, val) in dict {
            if let str = val as? String {
                fields[key] = ["stringValue": str]
            } else if let intVal = val as? Int {
                fields[key] = ["integerValue": "\(intVal)"]
            } else if let boolVal = val as? Bool {
                fields[key] = ["booleanValue": boolVal]
            } else if let subDict = val as? [String: Any] {
                fields[key] = ["mapValue": ["fields": encodeToFirestoreFields(subDict)]]
            }
        }
        return fields
    }

    private func decodeFromFirestoreFields(_ fields: [String: Any]) -> [String: Any] {
        var result: [String: Any] = [:]
        for (key, val) in fields {
            if let map = val as? [String: Any] {
                if let s = map["stringValue"] as? String {
                    result[key] = s
                } else if let i = map["integerValue"] as? String {
                    result[key] = Int(i) ?? 0
                } else if let b = map["booleanValue"] as? Bool {
                    result[key] = b
                } else if let mv = map["mapValue"] as? [String: Any], let subFields = mv["fields"] as? [String: Any] {
                    result[key] = decodeFromFirestoreFields(subFields)
                }
            }
        }
        return result
    }
}
