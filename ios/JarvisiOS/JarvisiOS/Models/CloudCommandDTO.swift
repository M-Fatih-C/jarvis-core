import Foundation

public enum CommandStatusDTO: String, Codable, Sendable {
    case queued
    case leased
    case running
    case waitingApproval = "waiting_approval"
    case completed
    case failed
    case cancelled
    case expired
}

public struct CloudCommandDTO: Identifiable, Codable, Sendable {
    public let id: String
    public let type: String
    public let name: String
    public let source_device: String
    public let target_device: String?
    public let payload: [String: AnyCodable]
    public let status: CommandStatusDTO
    public let idempotency_key: String
    public let created_at: String
    public let result: [String: AnyCodable]?
    public let error: String?

    public init(
        id: String,
        type: String = "agent_run",
        name: String = "chat",
        source_device: String = "ios_client",
        target_device: String? = "mac_mini_m4",
        payload: [String: AnyCodable] = [:],
        status: CommandStatusDTO = .queued,
        idempotency_key: String = UUID().uuidString,
        created_at: String = ISO8601DateFormatter().string(from: Date()),
        result: [String: AnyCodable]? = nil,
        error: String? = nil
    ) {
        self.id = id
        self.type = type
        self.name = name
        self.source_device = source_device
        self.target_device = target_device
        self.payload = payload
        self.status = status
        self.idempotency_key = idempotency_key
        self.created_at = created_at
        self.result = result
        self.error = error
    }
}

public struct AnyCodable: Codable, @unchecked Sendable, Equatable {
    public let value: Any

    public init(_ value: Any) {
        self.value = value
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let b = try? container.decode(Bool.self) { self.value = b }
        else if let i = try? container.decode(Int.self) { self.value = i }
        else if let d = try? container.decode(Double.self) { self.value = d }
        else if let s = try? container.decode(String.self) { self.value = s }
        else if let arr = try? container.decode([AnyCodable].self) { self.value = arr.map { $0.value } }
        else if let dict = try? container.decode([String: AnyCodable].self) { self.value = dict.mapValues { $0.value } }
        else { self.value = "" }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        if let b = value as? Bool { try container.encode(b) }
        else if let i = value as? Int { try container.encode(i) }
        else if let d = value as? Double { try container.encode(d) }
        else if let s = value as? String { try container.encode(s) }
        else if let arr = value as? [Any] {
            let encodableArr = arr.map { AnyCodable($0) }
            try container.encode(encodableArr)
        } else if let dict = value as? [String: Any] {
            let encodableDict = dict.mapValues { AnyCodable($0) }
            try container.encode(encodableDict)
        } else {
            try container.encode(String(describing: value))
        }
    }

    public static func == (lhs: AnyCodable, rhs: AnyCodable) -> Bool {
        String(describing: lhs.value) == String(describing: rhs.value)
    }
}
