import XCTest
@testable import JarvisMacAgentLib

final class RPCModelsTests: XCTestCase {
    func testRequestEncodingDecoding() throws {
        let req = RPCRequest(
            protocol_version: 1,
            id: "req-123",
            method: "calendar.list_events",
            params: [
                "start": AnyCodable("2026-10-01T10:00:00+03:00"),
                "limit": AnyCodable(10),
                "all_day": AnyCodable(false)
            ]
        )

        let data = try JSONEncoder().encode(req)
        let decoded = try JSONDecoder().decode(RPCRequest.self, from: data)

        XCTAssertEqual(decoded.protocol_version, 1)
        XCTAssertEqual(decoded.id, "req-123")
        XCTAssertEqual(decoded.method, "calendar.list_events")
        XCTAssertEqual(decoded.params["start"]?.value as? String, "2026-10-01T10:00:00+03:00")
        XCTAssertEqual(decoded.params["limit"]?.value as? Int, 10)
        XCTAssertEqual(decoded.params["all_day"]?.value as? Bool, false)
    }

    func testResponseSuccessEncodingDecoding() throws {
        let resp = RPCResponse.success(
            id: "req-123",
            result: AnyCodable(["created": true, "id": "event-999"])
        )

        let data = try JSONEncoder().encode(resp)
        let decoded = try JSONDecoder().decode(RPCResponse.self, from: data)

        XCTAssertEqual(decoded.protocol_version, 1)
        XCTAssertEqual(decoded.id, "req-123")
        XCTAssertTrue(decoded.ok)
        XCTAssertNil(decoded.error)
        let dict = decoded.result?.value as? [String: Sendable]
        XCTAssertEqual(dict?["created"] as? Bool, true)
        XCTAssertEqual(dict?["id"] as? String, "event-999")
    }

    func testResponseFailureEncodingDecoding() throws {
        let resp = RPCResponse.failure(
            id: "req-456",
            code: "PERMISSION_DENIED",
            message: "Calendar access is denied.",
            details: ["reason": AnyCodable("user_declined")]
        )

        let data = try JSONEncoder().encode(resp)
        let decoded = try JSONDecoder().decode(RPCResponse.self, from: data)

        XCTAssertEqual(decoded.protocol_version, 1)
        XCTAssertEqual(decoded.id, "req-456")
        XCTAssertFalse(decoded.ok)
        XCTAssertNil(decoded.result)
        XCTAssertEqual(decoded.error?.code, "PERMISSION_DENIED")
        XCTAssertEqual(decoded.error?.message, "Calendar access is denied.")
        XCTAssertEqual(decoded.error?.details?["reason"]?.value as? String, "user_declined")
    }

    func testAnyCodableEquality() {
        XCTAssertEqual(AnyCodable(42), AnyCodable(42))
        XCTAssertEqual(AnyCodable("hello"), AnyCodable("hello"))
        XCTAssertEqual(AnyCodable(true), AnyCodable(true))
        XCTAssertNotEqual(AnyCodable("hello"), AnyCodable("world"))
        XCTAssertEqual(AnyCodable(["a": 1, "b": 2]), AnyCodable(["b": 2, "a": 1]))
    }
}
