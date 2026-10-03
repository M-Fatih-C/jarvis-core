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
@MainActor
public final class FirebaseCommandQueueService {
    public static let shared = FirebaseCommandQueueService()
    private let session = URLSession.shared
    private func pendingCommandsKey(userId: String, projectId: String) -> String {
        "jarvis_pending_commands.\(projectId).\(userId)"
    }

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
        conversationId: String,
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
            "user_id": userId,
            "type": "agent_run",
            "name": "chat",
            "source_device": "iphone_13",
            "target_device": "mac-mini-main",
            "payload": [
                "input": message,
                "conversation_id": conversationId,
                "user_id": userId
            ],
            "status": "queued",
            "idempotency_key": idempotencyKey,
            "created_at": nowStr,
            "available_at": nowStr,
            "expires_at": ISO8601DateFormatter().string(from: Date().addingTimeInterval(600))
        ]

        // Persist identifier before transmission for retry safety
        rememberPendingCommand(commandId, userId: userId, projectId: projectId)

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
            "user_id": userId,
            "type": "approval_response",
            "name": "approval_decision",
            "source_device": "iphone_13",
            "target_device": "mac-mini-main",
            "payload": payloadData,
            "status": "queued",
            "idempotency_key": idempotencyKey,
            "created_at": nowStr,
            "available_at": nowStr,
            "expires_at": ISO8601DateFormatter().string(from: Date().addingTimeInterval(600))
        ]

        // Persist identifier before transmission for retry safety
        rememberPendingCommand(commandId, userId: userId, projectId: projectId)

        try await writeCommandDocument(commandId: commandId, userId: userId, projectId: projectId, payload: commandPayload)
        return commandId
    }

    /// Poll command status until completed, waiting for approval, failed, or timed out.
    public func pollCommand(
        commandId: String,
        userId: String,
        projectId: String,
        timeoutSeconds: Double = 180.0,
        onStatusChange: @escaping (CommandStatusDTO, [String: Any]?) -> Void
    ) async throws -> CloudCommandDTO {
        let start = Date()
        var lastStatus: CommandStatusDTO? = nil

        while Date().timeIntervalSince(start) < timeoutSeconds {
            try Task.checkCancellation()
            let fetched: CloudCommandDTO?
            do {
                fetched = try await fetchCommandDocument(commandId: commandId, userId: userId, projectId: projectId)
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                // Read retries never replay an operation or change its identifier.
                try await Task.sleep(nanoseconds: 3_000_000_000)
                continue
            }
            if let cmd = fetched {
                if cmd.status != lastStatus {
                    lastStatus = cmd.status
                    onStatusChange(cmd.status, cmd.result?.mapValues { $0.value })
                }

                if cmd.status == .completed {
                    forgetPendingCommand(commandId, userId: userId, projectId: projectId)
                    return cmd
                } else if cmd.status == .waitingApproval {
                    return cmd
                } else if [.failed, .cancelled, .expired].contains(cmd.status) {
                    forgetPendingCommand(commandId, userId: userId, projectId: projectId)
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
        let authToken = try await FirebaseAuthService.shared.validIDToken()

        guard let url = firestoreDocumentURL(projectId: projectId, path: "users/\(userId)/commands?documentId=\(commandId)") else {
            throw CommandQueueError.networkError("Geçersiz Firestore URL'si")
        }

        var request = URLRequest(url: url, timeoutInterval: 10)
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

        if httpRes.statusCode == 409 { return } // Existing immutable command: resume polling.
        guard httpRes.statusCode == 200 || httpRes.statusCode == 201 else {
            let errorBody = String(data: data, encoding: .utf8) ?? "Bilinmeyen hata"
            throw CommandQueueError.networkError("Firestore komut yazma reddedildi (HTTP \(httpRes.statusCode)): \(errorBody)")
        }
    }

    public func fetchCommandDocument(
        commandId: String,
        userId: String,
        projectId: String
    ) async throws -> CloudCommandDTO? {
        let authToken = try await FirebaseAuthService.shared.validIDToken()

        guard let url = firestoreDocumentURL(projectId: projectId, path: "users/\(userId)/commands/\(commandId)") else {
            throw CommandQueueError.networkError("Geçersiz Firestore URL'si")
        }

        var request = URLRequest(url: url, timeoutInterval: 10)
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

    public func fetchDeviceStatus(userId: String, projectId: String) async throws -> [String: Any] {
        let token = try await FirebaseAuthService.shared.validIDToken()
        guard let url = firestoreDocumentURL(projectId: projectId, path: "users/\(userId)/devices/mac-mini-main") else {
            throw CommandQueueError.invalidResponse
        }
        var request = URLRequest(url: url, timeoutInterval: 10)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let (data, response) = try await session.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200,
              let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let fields = json["fields"] as? [String: Any] else {
            throw CommandQueueError.networkError("Mac henüz bağlanmadı veya oturum geçersiz.")
        }
        return decodeFromFirestoreFields(fields)
    }

    // --- Idempotency & Safe Retry Tracking ---

    private func rememberPendingCommand(_ id: String, userId: String, projectId: String) {
        let pendingCommandsKey = pendingCommandsKey(userId: userId, projectId: projectId)
        var list = UserDefaults.standard.stringArray(forKey: pendingCommandsKey) ?? []
        if !list.contains(id) {
            list.append(id)
            UserDefaults.standard.set(list, forKey: pendingCommandsKey)
        }
    }

    public func forgetPendingCommand(_ id: String, userId: String, projectId: String) {
        let pendingCommandsKey = pendingCommandsKey(userId: userId, projectId: projectId)
        var list = UserDefaults.standard.stringArray(forKey: pendingCommandsKey) ?? []
        list.removeAll(where: { $0 == id })
        UserDefaults.standard.set(list, forKey: pendingCommandsKey)
    }

    public func getPendingCommandIds(userId: String, projectId: String) -> [String] {
        return UserDefaults.standard.stringArray(forKey: pendingCommandsKey(userId: userId, projectId: projectId)) ?? []
    }

    // --- Firestore Field Serialization ---

    /// Calendar screens use the same authenticated owner queue as chat. These
    /// read operations do not enter the chat's pending mutation recovery list.
    public func readCalendarTool(_ name: String, arguments: [String: Any] = [:], timeout: Double = 45) async throws -> [String: Any] {
        guard ["calendar.list_calendars", "calendar.list_events"].contains(name),
              let userId = KeychainHelper.shared.read(key: "firebase_user_uid"),
              let projectId = KeychainHelper.shared.read(key: "firebase_project_id") else {
            throw CommandQueueError.unauthenticated
        }
        let id = UUID().uuidString
        let formatter = ISO8601DateFormatter()
        let now = Date()
        let payload: [String: Any] = [
            "id": id, "user_id": userId, "type": "tool_execution", "name": name,
            "source_device": "iphone_13", "target_device": "mac-mini-main",
            "payload": ["arguments": arguments, "user_id": userId], "status": "queued",
            "idempotency_key": id, "created_at": formatter.string(from: now),
            "available_at": formatter.string(from: now), "expires_at": formatter.string(from: now.addingTimeInterval(180))
        ]
        try await writeCommandDocument(commandId: id, userId: userId, projectId: projectId, payload: payload)
        let result = try await pollCommand(commandId: id, userId: userId, projectId: projectId, timeoutSeconds: timeout) { _, _ in }
        guard let data = result.result?["data"]?.value as? [String: Any] else { throw CommandQueueError.invalidResponse }
        return data
    }

    private func encodeToFirestoreFields(_ dict: [String: Any]) -> [String: Any] {
        dict.mapValues(encodeFirestoreValue)
    }

    private func encodeFirestoreValue(_ value: Any) -> [String: Any] {
        if let value = value as? String { return ["stringValue": value] }
        if let value = value as? Bool { return ["booleanValue": value] }
        if let value = value as? Int { return ["integerValue": String(value)] }
        if let value = value as? Double { return ["doubleValue": value] }
        if let value = value as? [String: Any] { return ["mapValue": ["fields": encodeToFirestoreFields(value)]] }
        if let value = value as? [Any] { return ["arrayValue": ["values": value.map(encodeFirestoreValue)]] }
        return ["nullValue": NSNull()]
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
                } else if let d = map["doubleValue"] as? Double {
                    result[key] = d
                } else if map["nullValue"] != nil {
                    result[key] = NSNull()
                } else if let a = map["arrayValue"] as? [String: Any] {
                    result[key] = (a["values"] as? [[String: Any]] ?? []).map {
                        decodeFromFirestoreFields(["v": $0])["v"] ?? NSNull()
                    }
                } else if let mv = map["mapValue"] as? [String: Any], let subFields = mv["fields"] as? [String: Any] {
                    result[key] = decodeFromFirestoreFields(subFields)
                }
            }
        }
        return result
    }
}
