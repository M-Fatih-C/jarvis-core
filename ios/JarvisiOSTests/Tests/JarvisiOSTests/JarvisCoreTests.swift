import XCTest
@testable import JarvisiOSCore

final class JarvisCoreTests: XCTestCase {

    override func setUp() {
        super.setUp()
        KeychainHelper.shared.clearAll()
    }

    override func tearDown() {
        KeychainHelper.shared.clearAll()
        super.tearDown()
    }

    // MARK: - 1. Authentication State & Keychain Tests

    func testAuthenticationStateAndKeychainStorage() {
        let testUID = "user_m4_iphone13_test"
        let testToken = "firebase_jwt_token_sample_abc123"

        // Save to Keychain
        let uidSaved = KeychainHelper.shared.save(key: "auth_uid", value: testUID)
        let tokenSaved = KeychainHelper.shared.save(key: "auth_id_token", value: testToken)
        XCTAssertTrue(uidSaved, "UID must be successfully saved to Keychain")
        XCTAssertTrue(tokenSaved, "Token must be successfully saved to Keychain")

        // Read back from Keychain
        let readUID = KeychainHelper.shared.read(key: "auth_uid")
        let readToken = KeychainHelper.shared.read(key: "auth_id_token")
        XCTAssertEqual(readUID, testUID)
        XCTAssertEqual(readToken, testToken)

        // Verify delete / signOut
        let deleted = KeychainHelper.shared.delete(key: "auth_id_token")
        XCTAssertTrue(deleted)
        XCTAssertNil(KeychainHelper.shared.read(key: "auth_id_token"))
        XCTAssertEqual(KeychainHelper.shared.read(key: "auth_uid"), testUID)

        // Clear all
        KeychainHelper.shared.clearAll()
        XCTAssertNil(KeychainHelper.shared.read(key: "auth_uid"))
    }

    // MARK: - 2. Response Decoding Tests

    func testPythonChatResponseDecoding() throws {
        // Python API response schema: runId, status, message, optional approval
        let json = """
        {
            "runId": "run_987654",
            "status": "success",
            "message": "Jarvis 2 saatlik odak çalışma bloğu takviminize kaydedildi.",
            "approval": null
        }
        """.data(using: .utf8)!

        struct PythonChatResponse: Decodable {
            let runId: String
            let status: String
            let message: String
            let approval: String?
        }

        let decoded = try JSONDecoder().decode(PythonChatResponse.self, from: json)
        XCTAssertEqual(decoded.runId, "run_987654")
        XCTAssertEqual(decoded.status, "success")
        XCTAssertEqual(decoded.message, "Jarvis 2 saatlik odak çalışma bloğu takviminize kaydedildi.")
        XCTAssertNil(decoded.approval)
    }

    func testInvalidResponseDecodingDoesNotSilentlySucceed() {
        // Schema missing required fields
        let invalidJson = """
        {
            "wrong_field": 123
        }
        """.data(using: .utf8)!

        struct PythonChatResponse: Decodable {
            let runId: String
            let status: String
            let message: String
        }

        XCTAssertThrowsError(try JSONDecoder().decode(PythonChatResponse.self, from: invalidJson), "Must throw decoding error on invalid response schema")
    }

    // MARK: - 3. Command Lifecycle & Idempotency Tests

    func testCloudCommandLifecycleAndIdempotency() throws {
        let key1 = UUID().uuidString
        let key2 = UUID().uuidString
        XCTAssertNotEqual(key1, key2, "Idempotency keys must be distinct for each command")

        // 1. Create Queued Command
        let command = CloudCommandDTO(
            id: "cmd_001",
            type: "agent_run",
            name: "chat",
            source_device: "ios_client",
            payload: ["prompt": AnyCodable("Takvime 2 saatlik Jarvis çalışma bloğu ekle")],
            status: .queued,
            idempotency_key: key1
        )
        XCTAssertEqual(command.status, .queued)
        XCTAssertEqual(command.idempotency_key, key1)

        // 2. Encode to JSON and decode back
        let encoded = try JSONEncoder().encode(command)
        let decoded = try JSONDecoder().decode(CloudCommandDTO.self, from: encoded)
        XCTAssertEqual(decoded.id, "cmd_001")
        XCTAssertEqual(decoded.status, .queued)
        XCTAssertEqual(decoded.source_device, "ios_client")

        // 3. Test lifecycle status transitions
        let allStatuses: [CommandStatusDTO] = [
            .queued, .leased, .running, .waitingApproval, .completed, .failed, .cancelled, .expired
        ]
        for s in allStatuses {
            let cmd = CloudCommandDTO(
                id: "cmd_\(s.rawValue)",
                status: s,
                idempotency_key: UUID().uuidString
            )
            XCTAssertEqual(cmd.status, s)
        }
    }

    // MARK: - 4. Task Approval Lifecycle (7 States) & EventKit Synchronization

    func testTaskProposalSevenLifecycleStates() throws {
        // Required 7 lifecycle states:
        // PROPOSED -> WAITING_APPROVAL -> APPROVED -> EXECUTING -> EXECUTED -> FAILED -> DISMISSED
        let expectedStates: [TaskProposalStatusDTO] = [
            .proposed,
            .waitingApproval,
            .approved,
            .executing,
            .executed,
            .failed,
            .dismissed
        ]

        for state in expectedStates {
            let json = """
            {
                "task_id": "task_123",
                "source_message_id": "msg_456",
                "title": "Jarvis Çalışma Bloğu",
                "description": "2 saatlik odaklanma seansı",
                "category": "WORK",
                "priority": "HIGH",
                "deadline": "2026-10-04T12:00:00Z",
                "deadline_confidence": "HIGH",
                "raw_deadline_text": "Yarın öğlen",
                "proposed_action": "calendar_event",
                "status": "\(state.rawValue)",
                "created_at": "2026-10-03T12:00:00Z"
            }
            """.data(using: .utf8)!

            let proposal = try JSONDecoder().decode(TaskProposalDTO.self, from: json)
            XCTAssertEqual(proposal.status, state)
        }
    }

    func testTaskActionDigestAndVerification() throws {
        let actionJSON = """
        {
            "action_id": "act_789",
            "task_id": "task_123",
            "source_message_id": "msg_456",
            "action_type": "calendar_event",
            "target_destination": "Apple Calendar",
            "title": "Jarvis Çalışma Bloğu",
            "notes": "Qwen ile pair-programming seansı",
            "start_time": "2026-10-04T10:00:00Z",
            "end_time": "2026-10-04T12:00:00Z",
            "due_date": null,
            "target_calendar_id": "cal_primary",
            "approval_id": "appr_999",
            "action_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "status": "EXECUTED",
            "external_id": "EK_EVENT_ID_ABCXYZ",
            "error_message": null
        }
        """.data(using: .utf8)!

        let action = try JSONDecoder().decode(TaskActionDTO.self, from: actionJSON)
        XCTAssertEqual(action.status, .executed)
        XCTAssertEqual(action.external_id, "EK_EVENT_ID_ABCXYZ", "EXECUTED state must include real read-back external_id")
        XCTAssertNotNil(action.action_digest, "Must retain canonical action digest for server verification")
    }

    // MARK: - 5. Approval Presentation & Error Handling

    func testApprovalPresentationCardModel() throws {
        // Pending approval representation presented in chat
        let approvalJSON = """
        {
            "approval_id": "appr_555",
            "tool_name": "mac.calendar.create_event",
            "arguments": {
                "title": "Jarvis Odak Bloğu",
                "start": "2026-10-04 14:00:00",
                "end": "2026-10-04 16:00:00"
            },
            "action_digest": "4a5b6c7d8e9f",
            "status": "pending",
            "risk_level": "R2_WRITE"
        }
        """.data(using: .utf8)!

        struct ApprovalCardPayload: Decodable {
            let approval_id: String
            let tool_name: String
            let arguments: [String: String]
            let action_digest: String
            let status: String
            let risk_level: String
        }

        let card = try JSONDecoder().decode(ApprovalCardPayload.self, from: approvalJSON)
        XCTAssertEqual(card.approval_id, "appr_555")
        XCTAssertEqual(card.tool_name, "mac.calendar.create_event")
        XCTAssertEqual(card.risk_level, "R2_WRITE")
        XCTAssertEqual(card.arguments["title"], "Jarvis Odak Bloğu")
    }

    func testFailedCommandErrorPresentation() throws {
        let failedCommand = CloudCommandDTO(
            id: "cmd_err_01",
            status: .failed,
            error: "PolicyEngine: Operation R5_CRITICAL is strictly denied."
        )
        XCTAssertEqual(failedCommand.status, .failed)
        XCTAssertEqual(failedCommand.error, "PolicyEngine: Operation R5_CRITICAL is strictly denied.")
    }
}
