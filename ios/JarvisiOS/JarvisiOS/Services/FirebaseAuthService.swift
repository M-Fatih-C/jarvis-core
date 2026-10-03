import Foundation
import LocalAuthentication

public struct AuthSession: Codable, Sendable {
    public let uid: String
    public let email: String
    public let idToken: String
    public let refreshToken: String?

    public init(uid: String, email: String, idToken: String, refreshToken: String? = nil) {
        self.uid = uid
        self.email = email
        self.idToken = idToken
        self.refreshToken = refreshToken
    }
}

public enum AuthError: LocalizedError {
    case invalidCredentials(String)
    case missingConfiguration(String)
    case networkError(String)
    case unauthenticated
    case locked
    case decodingError

    public var errorDescription: String? {
        switch self {
        case .invalidCredentials(let msg):
            return "Kimlik doğrulama başarısız: \(msg)"
        case .missingConfiguration(let msg):
            return "Yapılandırma eksik: \(msg)"
        case .networkError(let msg):
            return "Ağ hatası: \(msg)"
        case .unauthenticated:
            return "Oturumun süresi doldu. Lütfen hesabına tekrar giriş yap."
        case .locked:
            return "Önce JARVIS’i Face ID veya cihaz parolasıyla aç."
        case .decodingError:
            return "Kimlik doğrulama sunucu yanıtı çözümlenemedi."
        }
    }
}

/// Communicates with Firebase Authentication service to authenticate users, manage sessions, and refresh tokens.
/// Fail-closed security: Never accepts fake development tokens or fabricated fallback sessions.
@MainActor
public final class FirebaseAuthService {
    public static let shared = FirebaseAuthService()
    private let session: URLSession
    private let vault: SessionVault
    private let configuration: () throws -> FirebaseConfiguration
    private var refreshTask: Task<String, Error>?
    private var generation = UUID()
    private var unlocked: ProtectedSession?
    private var context: LAContext?
    var onInvalidSession: (() -> Void)?

    init(vault: SessionVault = KeychainSessionVault(), session: URLSession = .shared,
         configuration: @escaping () throws -> FirebaseConfiguration = FirebaseConfiguration.bundled) {
        self.vault = vault; self.session = session; self.configuration = configuration
    }
    var hasSavedSession: Bool { vault.hasSession }
    var currentUserID: String? { unlocked?.auth.uid }
    var currentEmail: String { unlocked?.auth.email ?? "" }
    var currentProjectID: String? { unlocked?.projectID }
    var sessionIdentifier: UUID { generation }
    var isUnlocked: Bool { unlocked != nil }
    var historyKey: Data? { unlocked?.historyKey }

    func unlock(context: LAContext) throws {
        let config = try configuration()
        var stored = try vault.load(context: context, project: config.projectID)
        guard stored.projectID == config.projectID, !stored.auth.uid.isEmpty else { throw AuthError.unauthenticated }
        if stored.historyKey == nil {
            stored.prepareHistoryKey()
            try vault.save(stored, context: context)
        }
        generation = UUID()
        self.context = context
        unlocked = stored
    }

    func accept(_ auth: AuthSession, context: LAContext) throws {
        guard let refresh = auth.refreshToken, !refresh.isEmpty, !auth.uid.isEmpty else { throw AuthError.decodingError }
        var stored = ProtectedSession(projectID: try configuration().projectID, auth: auth)
        if let previous = try? vault.load(context: context, project: stored.projectID), previous.auth.uid == auth.uid {
            stored.historyKey = previous.historyKey
        }
        stored.prepareHistoryKey()
        try vault.save(stored, context: context)
        generation = UUID()
        self.context = context
        unlocked = stored
    }

    func lock() {
        generation = UUID()
        refreshTask?.cancel(); refreshTask = nil
        unlocked = nil
        context?.invalidate(); context = nil
    }

    func signOut() throws {
        lock()
        try vault.clear()
    }

    static func tokenExpiresSoon(_ token: String, now: Date = Date()) -> Bool {
        guard let claims = claims(token), let expires = claims["exp"] as? Double else { return true }
        return expires <= now.timeIntervalSince1970 + 120
    }
    private static func claims(_ token: String) -> [String: Any]? {
        let parts = token.split(separator: ".")
        guard parts.count == 3 else { return nil }
        var text = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        text += String(repeating: "=", count: (4 - text.count % 4) % 4)
        guard let data = Data(base64Encoded: text) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
    }

    /// Local biometric unlocking is separate from Firebase authorization. A JWT's
    /// decoded expiry is only a cache hint; the remote service validates its signature.
    public func validIDToken() async throws -> String {
        guard let stored = unlocked, let context else { throw AuthError.locked }
        if !Self.tokenExpiresSoon(stored.auth.idToken) { return stored.auth.idToken }
        if let existing = refreshTask { return try await existing.value }
        guard let refresh = stored.auth.refreshToken, !refresh.isEmpty else { throw AuthError.unauthenticated }
        let epoch = generation
        let task = Task<String, Error> {
            do {
                let renewed = try await self.refreshToken(refreshToken: refresh)
                guard self.generation == epoch, self.unlocked?.auth.uid == stored.auth.uid else { throw AuthError.locked }
                guard renewed.uid == stored.auth.uid else { throw AuthError.unauthenticated }
                var next = stored
                next.auth = AuthSession(uid: renewed.uid, email: stored.auth.email,
                    idToken: renewed.idToken, refreshToken: renewed.refreshToken)
                try self.vault.save(next, context: context)
                self.unlocked = next
                return renewed.idToken
            } catch AuthError.unauthenticated {
                if self.generation == epoch { self.invalidateSession() }
                throw AuthError.unauthenticated
            }
        }
        refreshTask = task
        defer { if generation == epoch { refreshTask = nil } }
        return try await task.value
    }

    /// Detect deleted/disabled/revoked accounts on foreground without renewing a
    /// still-valid token. Network failures preserve the protected session.
    func validateAccount() async throws {
        let epoch = generation
        let token = try await validIDToken()
        let config = try configuration()
        let url = URL(string: "https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=\(config.apiKey)")!
        var request = URLRequest(url: url, timeoutInterval: 10)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["idToken": token])
        let (data, response) = try await session.data(for: request)
        guard epoch == generation else { throw AuthError.locked }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        do {
            guard code == 200 else { throw Self.responseError(data, status: code) }
            guard let result = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let users = result["users"] as? [[String: Any]], let user = users.first,
                  user["localId"] as? String == currentUserID, user["disabled"] as? Bool != true else {
                throw AuthError.unauthenticated
            }
            if let since = user["validSince"] as? String, let revokedBefore = Double(since),
               let authenticatedAt = Self.claims(token)?["auth_time"] as? Double, authenticatedAt < revokedBefore {
                throw AuthError.unauthenticated
            }
        } catch AuthError.unauthenticated {
            invalidateSession()
            throw AuthError.unauthenticated
        }
    }

    private func invalidateSession() {
        lock()
        // Keep the UI locked even if a device Keychain deletion is temporarily unavailable.
        try? vault.clear()
        onInvalidSession?()
    }

    static func responseError(_ data: Data, status: Int) -> AuthError {
        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        let code = (json?["error"] as? [String: Any])?["message"] as? String ?? ""
        if ["INVALID_REFRESH_TOKEN", "TOKEN_EXPIRED", "USER_DISABLED", "USER_NOT_FOUND", "INVALID_ID_TOKEN"].contains(code) {
            return .unauthenticated
        }
        if status == 429 || status >= 500 || status == 0 { return .networkError("Bağlantı geçici olarak kullanılamıyor. Tekrar dene.") }
        return .invalidCredentials("Giriş bilgileri doğrulanamadı. E-posta ve parolanı kontrol et.")
    }

    public func resolveApiKey(providedKey: String? = nil) -> String? { try? configuration().apiKey }

    /// Authenticate against Firebase Auth Identity Toolkit API.
    /// Strictly fails closed on any authentication or network error.
    public func authenticate(email: String, password: String, apiKey: String? = nil, createAccount: Bool = false) async throws -> AuthSession {
        guard let resolvedKey = resolveApiKey(providedKey: apiKey) else {
            throw AuthError.missingConfiguration(
                "Uygulama bağlantısı yapılandırılamadı. JARVIS’in güncel sürümünü yükleyin."
            )
        }

        let payload: [String: Any] = [
            "email": email,
            "password": password,
            "returnSecureToken": true
        ]

        let action = createAccount ? "signUp" : "signInWithPassword"
        let endpoint = "https://identitytoolkit.googleapis.com/v1/accounts:\(action)?key=\(resolvedKey)"
        guard let url = URL(string: endpoint) else {
            throw AuthError.networkError("Geçersiz Auth uç noktası")
        }

        var request = URLRequest(url: url, timeoutInterval: 10)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try? JSONSerialization.data(withJSONObject: payload)

        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            // Fail closed: No offline fabrication permitted in production
            throw AuthError.networkError(error.localizedDescription)
        }

        guard let httpRes = response as? HTTPURLResponse else {
            throw AuthError.networkError("Sunucudan geçerli bir HTTP yanıtı alınamadı.")
        }

        if httpRes.statusCode == 200 {
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let uid = json["localId"] as? String,
                  let idToken = json["idToken"] as? String else {
                throw AuthError.decodingError
            }
            let userEmail = (json["email"] as? String) ?? email
            let refreshToken = json["refreshToken"] as? String
            return AuthSession(uid: uid, email: userEmail, idToken: idToken, refreshToken: refreshToken)
        } else {
            throw Self.responseError(data, status: httpRes.statusCode)
        }
    }

    /// Refresh token with Firebase Secure Token service.
    public func refreshToken(refreshToken: String, apiKey: String? = nil) async throws -> AuthSession {
        guard let resolvedKey = resolveApiKey(providedKey: apiKey) else {
            throw AuthError.missingConfiguration("Firebase API anahtarı bulunamadı.")
        }

        let endpoint = "https://securetoken.googleapis.com/v1/token?key=\(resolvedKey)"
        guard let url = URL(string: endpoint) else {
            throw AuthError.networkError("Geçersiz token yenileme uç noktası")
        }

        var request = URLRequest(url: url, timeoutInterval: 10)
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        var form = URLComponents()
        form.queryItems = [URLQueryItem(name: "grant_type", value: "refresh_token"), URLQueryItem(name: "refresh_token", value: refreshToken)]
        let bodyString = form.percentEncodedQuery ?? ""
        request.httpBody = bodyString.data(using: .utf8)

        let (data, response) = try await session.data(for: request)
        guard let httpRes = response as? HTTPURLResponse else { throw AuthError.networkError("Bağlantı yanıtı alınamadı.") }
        guard httpRes.statusCode == 200 else { throw Self.responseError(data, status: httpRes.statusCode) }

        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let newIdToken = json["id_token"] as? String,
              let userUID = json["user_id"] as? String else {
            throw AuthError.decodingError
        }

        let newRefresh = (json["refresh_token"] as? String) ?? refreshToken
        return AuthSession(uid: userUID, email: "", idToken: newIdToken, refreshToken: newRefresh)
    }
}
