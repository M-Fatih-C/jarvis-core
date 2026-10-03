import Foundation
import SwiftUI

/// Manages real Firebase Authentication state, monitoring, and secure session persistence via Keychain.
/// Does not silently authenticate as a predefined account.
@MainActor
public final class AuthViewModel: ObservableObject {
    @Published public var isAuthenticated: Bool = false
    @Published public var userId: String = ""
    @Published public var userEmail: String = ""
    @Published public var projectId: String = ""
    @Published public var apiKey: String = ""
    @Published public var isLoading: Bool = false
    @Published public var errorMessage: String? = nil

    private let keychain = KeychainHelper.shared
    private let keyUID = "firebase_user_uid"
    private let keyEmail = "firebase_user_email"
    private let keyToken = "firebase_id_token"
    private let keyRefreshToken = "firebase_refresh_token"
    private let keyProject = "firebase_project_id"
    private let keyApiKey = "firebase_api_key"

    private var bundledProject: String {
        guard let path = Bundle.main.path(forResource: "GoogleService-Info", ofType: "plist"),
              let dict = NSDictionary(contentsOfFile: path) as? [String: Any] else { return "" }
        return dict["PROJECT_ID"] as? String ?? ""
    }

    private var configuredProject: String {
        let stored = keychain.read(key: keyProject) ?? ""
        return stored.isEmpty || stored == "jarvis-local-dev" ? bundledProject : stored
    }

    public init() {
        restoreSession()
    }

    /// Restore previous authenticated session from secure Keychain storage.
    public func restoreSession() {
        if let storedUID = keychain.read(key: keyUID), !storedUID.isEmpty,
           let storedToken = keychain.read(key: keyToken), !storedToken.isEmpty {
            self.userId = storedUID
            self.userEmail = keychain.read(key: keyEmail) ?? ""
            self.projectId = configuredProject
            self.apiKey = keychain.read(key: keyApiKey) ?? ""
            self.isAuthenticated = false

            // Check if token can be refreshed automatically
            if let refresh = keychain.read(key: keyRefreshToken), !refresh.isEmpty {
                Task {
                    await self.refreshCurrentToken(refreshToken: refresh)
                }
            }
        } else {
            self.isAuthenticated = false
            self.userId = ""
            self.userEmail = ""
            self.apiKey = keychain.read(key: keyApiKey) ?? ""
            self.projectId = configuredProject
        }
    }

    /// Authenticate with Firebase using email and password.
    public func signIn(email: String, pass: String, createAccount: Bool = false) async -> Bool {
        guard !email.trimmingCharacters(in: .whitespaces).isEmpty, !pass.isEmpty else {
            self.errorMessage = "E-posta ve parola boş bırakılamaz."
            return false
        }

        self.isLoading = true
        self.errorMessage = nil

        do {
            let session = try await FirebaseAuthService.shared.authenticate(
                email: email.trimmingCharacters(in: .whitespaces),
                password: pass,
                apiKey: self.apiKey.isEmpty ? nil : self.apiKey,
                createAccount: createAccount
            )
            // Save to secure Keychain storage
            keychain.save(key: keyUID, value: session.uid)
            keychain.save(key: keyEmail, value: session.email)
            keychain.save(key: keyToken, value: session.idToken)
            if let ref = session.refreshToken {
                keychain.save(key: keyRefreshToken, value: ref)
            }
            keychain.save(key: keyProject, value: self.projectId)
            if !self.apiKey.isEmpty {
                keychain.save(key: keyApiKey, value: self.apiKey)
            }

            self.userId = session.uid
            self.userEmail = session.email
            self.isAuthenticated = true
            self.isLoading = false
            return true
        } catch {
            self.isLoading = false
            self.errorMessage = error.localizedDescription
            self.isAuthenticated = false
            return false
        }
    }

    /// Refresh token automatically
    public func refreshCurrentToken(refreshToken: String) async {
        do {
            _ = try await FirebaseAuthService.shared.validIDToken()
            self.isAuthenticated = true
        } catch {
            // Fail closed: If token is invalidated or revoked, sign out
            self.isAuthenticated = false
            self.errorMessage = error.localizedDescription
        }
    }

    /// Sign out and purge all authentication tokens from Keychain.
    public func signOut() {
        keychain.delete(key: keyUID)
        keychain.delete(key: keyEmail)
        keychain.delete(key: keyToken)
        keychain.delete(key: keyRefreshToken)
        self.isAuthenticated = false
        self.userId = ""
        self.userEmail = ""
        self.errorMessage = nil
    }

    public func updateProjectId(_ newId: String) {
        let trimmed = newId.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        self.projectId = trimmed
        keychain.save(key: keyProject, value: trimmed)
    }

    public func updateApiKey(_ newKey: String) {
        let trimmed = newKey.trimmingCharacters(in: .whitespaces)
        self.apiKey = trimmed
        if trimmed.isEmpty {
            keychain.delete(key: keyApiKey)
        } else {
            keychain.save(key: keyApiKey, value: trimmed)
        }
    }
}
