import Foundation

public struct AuthSession: Codable {
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
            return "Oturum açılmamış."
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
    private let session = URLSession.shared
    private var refreshTask: Task<String, Error>?

    private init() {}

    /// Refresh before expiry; Firestore still validates the signed token on every request.
    public func validIDToken() async throws -> String {
        let keys = KeychainHelper.shared
        if let token = keys.read(key: "firebase_id_token") {
            let parts = token.split(separator: ".")
            if parts.count == 3 {
                var b64 = String(parts[1]).replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
                b64 += String(repeating: "=", count: (4 - b64.count % 4) % 4)
                if let data = Data(base64Encoded: b64),
                   let claims = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                   let exp = claims["exp"] as? Double, exp > Date().timeIntervalSince1970 + 120 {
                    return token
                }
            }
        }
        if let existing = refreshTask { return try await existing.value }
        guard let refresh = keys.read(key: "firebase_refresh_token"), !refresh.isEmpty else {
            throw AuthError.unauthenticated
        }
        let expectedUID = keys.read(key: "firebase_user_uid")
        let task = Task<String, Error> {
            let renewed = try await self.refreshToken(refreshToken: refresh)
            guard renewed.uid == expectedUID,
                  keys.read(key: "firebase_refresh_token") == refresh else {
                throw AuthError.unauthenticated
            }
            keys.save(key: "firebase_id_token", value: renewed.idToken)
            if let next = renewed.refreshToken { keys.save(key: "firebase_refresh_token", value: next) }
            return renewed.idToken
        }
        refreshTask = task
        defer { refreshTask = nil }
        return try await task.value
    }

    /// Resolve Firebase API Key from GoogleService-Info.plist, Keychain, or environment.
    public func resolveApiKey(providedKey: String? = nil) -> String? {
        if let key = providedKey, !key.trimmingCharacters(in: .whitespaces).isEmpty {
            return key.trimmingCharacters(in: .whitespaces)
        }
        if let stored = KeychainHelper.shared.read(key: "firebase_api_key"), !stored.isEmpty {
            return stored
        }
        if let path = Bundle.main.path(forResource: "GoogleService-Info", ofType: "plist"),
           let dict = NSDictionary(contentsOfFile: path) as? [String: Any],
           let apiKey = dict["API_KEY"] as? String, !apiKey.isEmpty {
            return apiKey
        }
        return nil
    }

    /// Authenticate against Firebase Auth Identity Toolkit API.
    /// Strictly fails closed on any authentication or network error.
    public func authenticate(email: String, password: String, apiKey: String? = nil, createAccount: Bool = false) async throws -> AuthSession {
        guard let resolvedKey = resolveApiKey(providedKey: apiKey) else {
            throw AuthError.missingConfiguration(
                "Firebase API anahtarı bulunamadı. Lütfen GoogleService-Info.plist ekleyin veya ayarlardan API anahtarı girin."
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
            // Fail closed: Extract official Firebase error code and reject
            var detail = "HTTP \(httpRes.statusCode)"
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let errObj = json["error"] as? [String: Any],
               let msg = errObj["message"] as? String {
                detail = msg
            }
            throw AuthError.invalidCredentials(detail)
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
        guard let httpRes = response as? HTTPURLResponse, httpRes.statusCode == 200 else {
            throw AuthError.unauthenticated
        }

        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let newIdToken = json["id_token"] as? String,
              let userUID = json["user_id"] as? String else {
            throw AuthError.decodingError
        }

        let newRefresh = (json["refresh_token"] as? String) ?? refreshToken
        return AuthSession(uid: userUID, email: "", idToken: newIdToken, refreshToken: newRefresh)
    }
}
