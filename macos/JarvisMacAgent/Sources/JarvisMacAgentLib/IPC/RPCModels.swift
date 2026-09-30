import Foundation

public struct RPCRequest: Codable, Equatable, Sendable {
    public let protocol_version: Int
    public let id: String
    public let method: String
    public let params: [String: AnyCodable]

    public init(protocol_version: Int = 1, id: String = UUID().uuidString, method: String, params: [String: AnyCodable] = [:]) {
        self.protocol_version = protocol_version
        self.id = id
        self.method = method
        self.params = params
    }
}

public struct RPCResponse: Codable, Equatable, Sendable {
    public let protocol_version: Int
    public let id: String
    public let ok: Bool
    public let result: AnyCodable?
    public let error: RPCError?

    public init(protocol_version: Int = 1, id: String, ok: Bool, result: AnyCodable? = nil, error: RPCError? = nil) {
        self.protocol_version = protocol_version
        self.id = id
        self.ok = ok
        self.result = result
        self.error = error
    }

    public static func success(id: String, result: AnyCodable?) -> RPCResponse {
        RPCResponse(id: id, ok: true, result: result, error: nil)
    }

    public static func failure(id: String, code: String, message: String, details: [String: AnyCodable]? = nil) -> RPCResponse {
        RPCResponse(id: id, ok: false, result: nil, error: RPCError(code: code, message: message, details: details))
    }
}

public struct RPCError: Codable, Equatable, Sendable {
    public let code: String
    public let message: String
    public let details: [String: AnyCodable]?

    public init(code: String, message: String, details: [String: AnyCodable]? = nil) {
        self.code = code
        self.message = message
        self.details = details
    }
}

public struct AnyCodable: Codable, Equatable, Sendable {
    public let value: Sendable

    public init(_ value: Sendable) {
        self.value = value
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self.value = NSNull()
        } else if let bool = try? container.decode(Bool.self) {
            self.value = bool
        } else if let int = try? container.decode(Int.self) {
            self.value = int
        } else if let double = try? container.decode(Double.self) {
            self.value = double
        } else if let string = try? container.decode(String.self) {
            self.value = string
        } else if let array = try? container.decode([AnyCodable].self) {
            self.value = array.map { $0.value }
        } else if let dict = try? container.decode([String: AnyCodable].self) {
            self.value = dict.mapValues { $0.value }
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "AnyCodable unsupported value")
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self.value {
        case is NSNull:
            try container.encodeNil()
        case let bool as Bool:
            try container.encode(bool)
        case let int as Int:
            try container.encode(int)
        case let double as Double:
            try container.encode(double)
        case let string as String:
            try container.encode(string)
        case let array as [Sendable]:
            try container.encode(array.map { AnyCodable($0) })
        case let dict as [String: Sendable]:
            try container.encode(dict.mapValues { AnyCodable($0) })
        default:
            try container.encodeNil()
        }
    }

    public static func == (lhs: AnyCodable, rhs: AnyCodable) -> Bool {
        switch (lhs.value, rhs.value) {
        case (is NSNull, is NSNull):
            return true
        case (let l as Bool, let r as Bool):
            return l == r
        case (let l as Int, let r as Int):
            return l == r
        case (let l as Double, let r as Double):
            return l == r
        case (let l as String, let r as String):
            return l == r
        case (let l as [Sendable], let r as [Sendable]):
            guard l.count == r.count else { return false }
            return zip(l, r).allSatisfy { AnyCodable($0) == AnyCodable($1) }
        case (let l as [String: Sendable], let r as [String: Sendable]):
            guard l.count == r.count else { return false }
            for (key, val) in l {
                guard let rVal = r[key], AnyCodable(val) == AnyCodable(rVal) else { return false }
            }
            return true
        default:
            return false
        }
    }
}
