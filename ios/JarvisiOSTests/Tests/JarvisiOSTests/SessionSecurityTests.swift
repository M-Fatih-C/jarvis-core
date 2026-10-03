import XCTest
import LocalAuthentication
import CryptoKit
@testable import JarvisiOSCore

private final class MemoryVault: SessionVault {
    var value: ProtectedSession?
    var writes = 0
    var clears = 0
    var rejectSave = false
    var hasSession: Bool { value != nil }
    func load(context: LAContext, project: String) throws -> ProtectedSession {
        guard let value else { throw AuthError.unauthenticated }; return value
    }
    func save(_ session: ProtectedSession, context: LAContext) throws {
        if rejectSave { throw SessionVaultError(status: -1) }
        writes += 1; value = session
    }
    func clear() throws { clears += 1; value = nil }
}
@MainActor private final class AuthenticatorStub: LocalAuthenticating {
    var calls = 0
    var error: Error?
    var delay: UInt64 = 0
    func authorize() async throws -> LAContext {
        calls += 1
        if delay > 0 { try await Task.sleep(nanoseconds: delay) }
        if let error { throw error }
        return LAContext()
    }
    func cancel() {}
}
private final class AuthHTTPStub: URLProtocol {
    static var handler: ((URLRequest) throws -> (Int, [String: Any]))?
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw URLError(.notConnectedToInternet) }
            let (status, body) = try handler(request)
            let response = HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: try JSONSerialization.data(withJSONObject: body))
            client?.urlProtocolDidFinishLoading(self)
        } catch { client?.urlProtocol(self, didFailWithError: error) }
    }
    override func stopLoading() {}
}

@MainActor
final class SessionSecurityTests: XCTestCase {
    private func jwt(exp: TimeInterval = Date().timeIntervalSince1970 + 3600, authTime: Double = 10) -> String {
        let data = try! JSONSerialization.data(withJSONObject: ["exp": exp, "auth_time": authTime])
        return "test." + data.base64EncodedString().replacingOccurrences(of: "=", with: "").replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_") + ".synthetic"
    }
    private func fixture(expired: Bool = false) -> (FirebaseAuthService, MemoryVault) {
        let vault = MemoryVault()
        var stored = ProtectedSession(projectID: "test-project", auth: AuthSession(uid: "owner", email: "owner@example.invalid",
            idToken: jwt(exp: Date().timeIntervalSince1970 + (expired ? -10 : 3600)), refreshToken: "synthetic-refresh"))
        stored.prepareHistoryKey(); vault.value = stored
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [AuthHTTPStub.self]
        let service = FirebaseAuthService(vault: vault, session: URLSession(configuration: config),
            configuration: { FirebaseConfiguration(projectID: "test-project", apiKey: "synthetic-key") })
        AuthHTTPStub.handler = { _ in (200, ["users": [["localId": "owner", "validSince": "1"]]]) }
        return (service, vault)
    }

    func testStartupCannotUseStoredCredentialsBeforeLocalAuthentication() async throws {
        let (service, vault) = fixture()
        XCTAssertTrue(service.hasSavedSession)
        XCTAssertNil(service.currentUserID)
        do { _ = try await service.validIDToken(); XCTFail("Locked credentials leaked") }
        catch AuthError.locked {} catch { XCTFail("Unexpected error") }
        XCTAssertNotNil(vault.value)
    }

    func testCancelledBiometryDoesNotOpenOrEraseExistingSession() async {
        let (service, vault) = fixture()
        let local = AuthenticatorStub(); local.error = LAError(.userCancel)
        let view = AuthViewModel(service: service, authenticator: local)
        await view.becameActive(); await view.becameActive()
        XCTAssertFalse(view.isAuthenticated)
        XCTAssertEqual(local.calls, 1, "A cancelled automatic prompt must not loop")
        XCTAssertEqual(vault.clears, 0)
        XCTAssertTrue(view.hasSavedSession)
    }

    func testOverlappingForegroundEventsHaveOnePromptAndBackgroundRelocks() async {
        let (service, vault) = fixture()
        let local = AuthenticatorStub(); local.delay = 15_000_000
        let view = AuthViewModel(service: service, authenticator: local)
        async let one: Void = view.becameActive()
        async let two: Void = view.becameActive()
        _ = await (one, two)
        XCTAssertEqual(local.calls, 1)
        XCTAssertTrue(view.isAuthenticated)
        XCTAssertEqual(view.userId, "owner")
        view.enteredBackground()
        XCTAssertFalse(view.isAuthenticated)
        XCTAssertNil(service.historyKey)
        XCTAssertNotNil(vault.value)
        await view.becameActive()
        XCTAssertEqual(local.calls, 2)
        XCTAssertTrue(view.isAuthenticated)
    }

    func testLateLocalAuthenticationCannotUnlockAfterBackground() async {
        let (service, vault) = fixture()
        let local = AuthenticatorStub(); local.delay = 40_000_000
        let view = AuthViewModel(service: service, authenticator: local)
        let task = Task { await view.becameActive() }
        await Task.yield()
        view.enteredBackground()
        await task.value
        XCTAssertFalse(view.isAuthenticated)
        XCTAssertFalse(service.isUnlocked)
        XCTAssertEqual(vault.clears, 0)
    }

    func testValidTokenIsReusedWithoutNetworkOrSecondLocalPrompt() async throws {
        let (service, vault) = fixture()
        AuthHTTPStub.handler = { _ in XCTFail("Unnecessary token request"); throw URLError(.badServerResponse) }
        try service.unlock(context: LAContext())
        let actual = try await service.validIDToken()
        XCTAssertEqual(actual, vault.value?.auth.idToken)
        XCTAssertEqual(vault.writes, 0)
    }

    func testExpiredTokenRefreshIsSingleFlightAndPreservesHistoryKey() async throws {
        let (service, vault) = fixture(expired: true)
        let originalKey = vault.value?.historyKey
        let renewed = jwt()
        var calls = 0
        AuthHTTPStub.handler = { req in
            XCTAssertEqual(req.url?.host, "securetoken.googleapis.com")
            calls += 1
            return (200, ["user_id": "owner", "id_token": renewed, "refresh_token": "renewed-refresh"])
        }
        try service.unlock(context: LAContext())
        async let one = service.validIDToken()
        async let two = service.validIDToken()
        let tokens = try await [one, two]
        XCTAssertEqual(tokens, [renewed, renewed])
        XCTAssertEqual(calls, 1)
        XCTAssertEqual(vault.writes, 1)
        XCTAssertEqual(vault.value?.historyKey, originalKey)
    }

    func testTransientRefreshFailurePreservesSessionForRetry() async throws {
        let (service, vault) = fixture(expired: true)
        AuthHTTPStub.handler = { _ in (503, ["error": ["message": "UNAVAILABLE"]]) }
        try service.unlock(context: LAContext())
        do { _ = try await service.validIDToken(); XCTFail("Should fail") } catch {}
        XCTAssertEqual(vault.clears, 0)
        XCTAssertTrue(service.isUnlocked)
        XCTAssertNotNil(vault.value)
    }

    func testRevokedRefreshTokenClearsCredentialsAndPresentation() async throws {
        let (service, vault) = fixture(expired: true)
        AuthHTTPStub.handler = { _ in (400, ["error": ["message": "TOKEN_EXPIRED"]]) }
        let view = AuthViewModel(service: service, authenticator: AuthenticatorStub())
        await view.becameActive()
        do { _ = try await service.validIDToken() } catch {}
        XCTAssertNil(vault.value)
        XCTAssertFalse(view.isAuthenticated)
        XCTAssertFalse(service.isUnlocked)
    }

    func testServerRevocationTimeInvalidatesLocallyUnexpiredToken() async throws {
        let (service, vault) = fixture()
        AuthHTTPStub.handler = { _ in (200, ["users": [["localId": "owner", "validSince": "100"]]]) }
        try service.unlock(context: LAContext())
        do { try await service.validateAccount(); XCTFail("Revoked account accepted") } catch {}
        XCTAssertNil(vault.value)
        XCTAssertFalse(service.isUnlocked)
    }

    func testSignOutClearsActualVaultAndBlocksTokenUse() async throws {
        let (service, vault) = fixture()
        try service.unlock(context: LAContext())
        try service.signOut()
        XCTAssertEqual(vault.clears, 1)
        XCTAssertNil(vault.value)
        do { _ = try await service.validIDToken(); XCTFail("Signed out token returned") } catch {}
    }

    func testFailedProtectedWriteKeepsPreviousCredentialsAndDoesNotOpenReplacement() throws {
        let (service, vault) = fixture()
        vault.rejectSave = true
        XCTAssertThrowsError(try service.accept(AuthSession(uid: "replacement", email: "another@example.invalid", idToken: jwt(), refreshToken: "replacement-refresh"), context: LAContext()))
        XCTAssertEqual(vault.value?.auth.uid, "owner")
        XCTAssertFalse(service.isUnlocked)
    }

    func testEncryptedHistoryRestoresAfterRestartAndRejectsWrongAccountOrKey() throws {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: dir) }
        let store = ChatHistoryStore(directory: dir)
        let key = Data(repeating: 7, count: 32)
        let messages = [ChatMessageItem(sender: .user, text: "Synthetic private history")]
        try store.save(messages, account: "project.owner", key: key)
        let disk = try Data(contentsOf: store.file(account: "project.owner"))
        XCTAssertFalse(String(decoding: disk, as: UTF8.self).contains("Synthetic private history"))
        XCTAssertEqual(try ChatHistoryStore(directory: dir).load(account: "project.owner", key: key), messages)
        XCTAssertThrowsError(try store.load(account: "project.owner", key: Data(repeating: 8, count: 32)))
        try disk.write(to: store.file(account: "project.other"))
        XCTAssertThrowsError(try store.load(account: "project.other", key: key))
    }
}
