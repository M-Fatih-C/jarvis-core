import Foundation
import LocalAuthentication
import Security
import CryptoKit

struct FirebaseConfiguration {
    let projectID: String
    let apiKey: String
    static func bundled() throws -> FirebaseConfiguration {
        guard let url = Bundle.main.url(forResource: "GoogleService-Info", withExtension: "plist"),
              let data = try? Data(contentsOf: url),
              let values = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              let project = values["PROJECT_ID"] as? String, project == "jarvis-core-mfatihc",
              let key = values["API_KEY"] as? String, !key.isEmpty,
              values["BUNDLE_ID"] as? String == Bundle.main.bundleIdentifier else {
            throw AuthError.missingConfiguration("Uygulama bağlantı ayarları eksik. JARVIS’in güncel sürümünü yükleyin.")
        }
        return FirebaseConfiguration(projectID: project, apiKey: key)
    }
}

struct ProtectedSession: Codable {
    let projectID: String
    var auth: AuthSession
    var historyKey: Data? = nil

    mutating func prepareHistoryKey() {
        if historyKey == nil { historyKey = SymmetricKey(size: .bits256).withUnsafeBytes { Data($0) } }
    }
}

protocol SessionVault {
    var hasSession: Bool { get }
    func load(context: LAContext, project: String) throws -> ProtectedSession
    func save(_ session: ProtectedSession, context: LAContext) throws
    func clear() throws
}

struct SessionVaultError: LocalizedError {
    let status: OSStatus
    var errorDescription: String? { "Güvenli oturuma erişilemedi. Face ID veya cihaz parolasıyla tekrar deneyin. (\(status))" }
}

/// Migration writes and verifies the protected item before removing legacy tokens.
/// userPresence permits Face ID/Touch ID or the device passcode, including recovery
/// after biometric enrollment changes. Tokens never fall back to unprotected storage.
final class KeychainSessionVault: SessionVault {
    private let keys: KeychainHelper
    private let service: String
    private let account = "firebase_session_v2"
    private let legacyKeys = ["firebase_id_token", "firebase_refresh_token", "firebase_user_uid", "firebase_user_email", "firebase_project_id", "firebase_api_key"]
    init(service: String = "com.mfatihc.jarvis.auth") {
        self.service = service
        keys = KeychainHelper(serviceName: service)
    }
    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }
    private var hasProtected: Bool {
        var query = query
        query[kSecReturnAttributes as String] = true
        let context = LAContext()
        context.interactionNotAllowed = true
        query[kSecUseAuthenticationContext as String] = context
        let status = SecItemCopyMatching(query as CFDictionary, nil)
        return status == errSecSuccess || status == errSecInteractionNotAllowed || status == errSecAuthFailed
    }
    var hasSession: Bool { hasProtected || keys.read(key: "firebase_refresh_token") != nil }

    func load(context: LAContext, project: String) throws -> ProtectedSession {
        if hasProtected {
            let session = try readProtected(context: context)
            guard session.projectID == project else { throw AuthError.unauthenticated }
            try removeLegacy()
            return session
        }
        guard let uid = keys.read(key: "firebase_user_uid"), !uid.isEmpty,
              let refresh = keys.read(key: "firebase_refresh_token"), !refresh.isEmpty else { throw AuthError.unauthenticated }
        let legacyProject = keys.read(key: "firebase_project_id")
        guard legacyProject == nil || legacyProject == project else { throw AuthError.unauthenticated }
        let session = ProtectedSession(projectID: project, auth: AuthSession(uid: uid,
            email: keys.read(key: "firebase_user_email") ?? "", idToken: keys.read(key: "firebase_id_token") ?? "", refreshToken: refresh))
        try save(session, context: context)
        return session
    }

    private func readProtected(context: LAContext) throws -> ProtectedSession {
        var query = query
        query[kSecReturnData as String] = true
        query[kSecUseAuthenticationContext as String] = context
        context.interactionNotAllowed = true
        var value: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &value)
        guard status == errSecSuccess, let data = value as? Data else { throw SessionVaultError(status: status) }
        return try JSONDecoder().decode(ProtectedSession.self, from: data)
    }

    func save(_ session: ProtectedSession, context: LAContext) throws {
        let data = try JSONEncoder().encode(session)
        var lookup = query
        lookup[kSecUseAuthenticationContext as String] = context
        context.interactionNotAllowed = true
        var status = SecItemUpdate(lookup as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecItemNotFound {
            var error: Unmanaged<CFError>?
            guard let control = SecAccessControlCreateWithFlags(nil, kSecAttrAccessibleWhenPasscodeSetThisDeviceOnly, .userPresence, &error) else {
                throw SessionVaultError(status: errSecParam)
            }
            var add = query
            add[kSecValueData as String] = data
            add[kSecAttrAccessControl as String] = control
            add[kSecUseAuthenticationContext as String] = context
            status = SecItemAdd(add as CFDictionary, nil)
        }
        guard status == errSecSuccess else { throw SessionVaultError(status: status) }
        let verified = try readProtected(context: context)
        guard verified.projectID == session.projectID, verified.historyKey == session.historyKey,
              verified.auth.uid == session.auth.uid, verified.auth.refreshToken == session.auth.refreshToken,
              verified.auth.idToken == session.auth.idToken else { throw SessionVaultError(status: errSecDecode) }
        try removeLegacy()
    }

    private func removeLegacy() throws {
        for key in legacyKeys where !keys.delete(key: key) { throw SessionVaultError(status: errSecInteractionNotAllowed) }
    }
    func clear() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw SessionVaultError(status: status) }
        try removeLegacy()
    }
}

@MainActor
protocol LocalAuthenticating {
    func authorize() async throws -> LAContext
    func cancel()
}

@MainActor
final class DeviceOwnerAuthenticator: LocalAuthenticating {
    private var context: LAContext?
    func authorize() async throws -> LAContext {
        let context = LAContext()
        self.context = context
        context.localizedCancelTitle = "Vazgeç"
        context.localizedFallbackTitle = "Cihaz parolası"
        var error: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) else {
            throw error ?? LAError(.passcodeNotSet)
        }
        guard try await context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "JARVIS oturumunu güvenle aç") else {
            throw LAError(.authenticationFailed)
        }
        return context
    }
    func cancel() { context?.invalidate(); context = nil }
}
