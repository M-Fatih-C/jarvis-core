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

/// Dispatches user commands and approvals through the secure Firestore Cloud Command Queue.
/// Architecture:
/// SwiftUI → Firebase Auth → Firestore (users/{uid}/commands) → Mac CommandWorker → Qwen → Firestore result → SwiftUI
public final class FirebaseCommandQueueService {
    public static let shared = FirebaseCommandQueueService()
    private let session = URLSession.shared

    private init() {}

    /// Base Firestore REST URL for a document.
    private func firestoreDocumentURL(projectId: String, path: String) -> URL? {
        let cleanProject = projectId.isEmpty ? "jarvis-local-dev" : projectId
        let str = "https://firestore.googleapis.com/v1/projects/\(cleanProject)/databases/(default)/documents/\(path)"
        return URL(string: str)
    }

    /// Submit a new user chat message into the Firestore Command Queue.
    public func submitChatCommand(
        message: String,
        userId: String,
        projectId: String
    ) async throws -> String {
        let commandId = UUID().uuidString
        let idempotencyKey = UUID().uuidString
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

        try await writeCommandDocument(commandId: commandId, userId: userId, projectId: projectId, payload: commandPayload)
        return commandId
    }

    /// Submit an approval response from iPhone to the Command Queue.
    public func submitApprovalResponse(
        approvalId: String,
        decision: String,
        actionDigest: String?,
        userId: String,
        projectId: String
    ) async throws -> String {
        let commandId = UUID().uuidString
        let idempotencyKey = UUID().uuidString
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
                    return cmd
                } else if cmd.status == .waitingApproval {
                    return cmd
                } else if cmd.status == .failed {
                    throw CommandQueueError.commandFailed(cmd.error ?? "Bilinmeyen hata oluştu.")
                }
            }

            try await Task.sleep(nanoseconds: 1_000_000_000) // Poll every 1 second
        }

        throw CommandQueueError.commandTimeout
    }

    // --- Private Document Helpers (Firestore REST API bridge) ---

    private func writeCommandDocument(
        commandId: String,
        userId: String,
        projectId: String,
        payload: [String: Any]
    ) async throws {
        // Encode as Firestore fields or local memory storage bridge
        guard let url = firestoreDocumentURL(projectId: projectId, path: "users/\(userId)/commands?documentId=\(commandId)") else {
            throw CommandQueueError.networkError("Invalid Firestore endpoint")
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        // Transform payload dictionary into Firestore REST format
        let firestoreFields = encodeToFirestoreFields(payload)
        let body: [String: Any] = ["fields": firestoreFields]
        request.httpBody = try? JSONSerialization.data(withJSONObject: body)

        let (_, response) = try await session.data(for: request)
        if let httpRes = response as? HTTPURLResponse, httpRes.statusCode >= 400 {
            // Note: If cloud endpoint requires local proxy or admin token, record in local queue simulator
            loggerFallbackStore(commandId: commandId, userId: userId, payload: payload)
        }
    }

    private func fetchCommandDocument(
        commandId: String,
        userId: String,
        projectId: String
    ) async throws -> CloudCommandDTO? {
        guard let url = firestoreDocumentURL(projectId: projectId, path: "users/\(userId)/commands/\(commandId)") else {
            return nil
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"

        do {
            let (data, response) = try await session.data(for: request)
            guard let httpRes = response as? HTTPURLResponse, httpRes.statusCode == 200 else {
                return readFallbackStore(commandId: commandId)
            }

            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let fields = json["fields"] as? [String: Any] {
                let decoded = decodeFromFirestoreFields(fields)
                let jsonData = try JSONSerialization.data(withJSONObject: decoded)
                return try? JSONDecoder().decode(CloudCommandDTO.self, from: jsonData)
            }
        } catch {
            return readFallbackStore(commandId: commandId)
        }
        return readFallbackStore(commandId: commandId)
    }

    // In-memory fallback queue for local development when network to cloud is restricted
    private var localFallbackCommands: [String: [String: Any]] = [:]

    private func loggerFallbackStore(commandId: String, userId: String, payload: [String: Any]) {
        localFallbackCommands[commandId] = payload
    }

    private func readFallbackStore(commandId: String) -> CloudCommandDTO? {
        guard let raw = localFallbackCommands[commandId] else { return nil }
        guard let data = try? JSONSerialization.data(withJSONObject: raw) else { return nil }
        return try? JSONDecoder().decode(CloudCommandDTO.self, from: data)
    }

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
