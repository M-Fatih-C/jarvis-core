import Foundation
import CryptoKit

/// Bounded encrypted cache. Its key lives inside the user-presence protected
/// session; Firestore remains the recovery source after explicit sign-out.
struct ChatHistoryStore {
    var directory: URL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("ChatHistory", isDirectory: true)
    func file(account: String) -> URL {
        let name = SHA256.hash(data: Data(account.utf8)).map { String(format: "%02x", $0) }.joined()
        return directory.appendingPathComponent(name + ".encrypted")
    }
    func load(account: String, key: Data) throws -> [ChatMessageItem] {
        let url = file(account: account)
        guard FileManager.default.fileExists(atPath: url.path) else { return [] }
        let box = try AES.GCM.SealedBox(combined: Data(contentsOf: url))
        let clear = try AES.GCM.open(box, using: SymmetricKey(data: key), authenticating: Data(account.utf8))
        return try JSONDecoder().decode([ChatMessageItem].self, from: clear)
    }
    func save(_ messages: [ChatMessageItem], account: String, key: Data) throws {
        let clear = try JSONEncoder().encode(Array(messages.suffix(200)))
        let sealed = try AES.GCM.seal(clear, using: SymmetricKey(data: key), authenticating: Data(account.utf8))
        guard let encrypted = sealed.combined else { throw CocoaError(.fileWriteUnknown) }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        #if os(iOS)
        try encrypted.write(to: file(account: account), options: [.atomic, .completeFileProtection])
        #else
        try encrypted.write(to: file(account: account), options: .atomic)
        #endif
    }
}
