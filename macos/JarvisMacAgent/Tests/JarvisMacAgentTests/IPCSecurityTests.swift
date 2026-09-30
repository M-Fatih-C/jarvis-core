import XCTest
@testable import JarvisMacAgentLib

final class IPCSecurityTests: XCTestCase {
    func testDefaultSocketPath() {
        let path = IPCSecurity.defaultSocketPath()
        XCTAssertTrue(path.hasPrefix("/tmp/jarvis-"))
        XCTAssertTrue(path.hasSuffix("/mac-agent.sock"))
    }

    func testPrepareSocketDirectoryAndCleanup() throws {
        let testDir = "/tmp/jarvis-test-\(UUID().uuidString)"
        let testSocket = "\(testDir)/test.sock"

        try IPCSecurity.prepareSocketDirectory(for: testSocket)

        var isDir: ObjCBool = false
        XCTAssertTrue(FileManager.default.fileExists(atPath: testDir, isDirectory: &isDir))
        XCTAssertTrue(isDir.boolValue)

        // Create dummy file at socket path
        FileManager.default.createFile(atPath: testSocket, contents: "test".data(using: .utf8), attributes: nil)
        XCTAssertTrue(FileManager.default.fileExists(atPath: testSocket))

        // Running prepare again should cleanly unlink stale socket file
        try IPCSecurity.prepareSocketDirectory(for: testSocket)
        XCTAssertFalse(FileManager.default.fileExists(atPath: testSocket))

        // Clean up test dir
        try? FileManager.default.removeItem(atPath: testDir)
    }
}
