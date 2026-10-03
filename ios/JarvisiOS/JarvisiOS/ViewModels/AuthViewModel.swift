import Foundation
import SwiftUI

/// Manages real Firebase Authentication state, monitoring, and secure session persistence via Keychain.
/// Does not silently authenticate as a predefined account.
@MainActor
public final class AuthViewModel: ObservableObject {
    @Published public var isAuthenticated: Bool = false
    @Published public var userId: String = ""
    @Published public var userEmail: String = ""
    @Published public var projectId: String = "jarvis-local-dev"
    @Published public var isLoading: Bool = false
    @Published public var errorMessage: String? = nil

    private let keychain = KeychainHelper.shared
    private let keyUID = "firebase_user_uid"
    private let keyEmail = "firebase_user_email"
    private let keyToken = "firebase_id_token"
    private let keyProject = "firebase_project_id"

    public init() {
        restoreSession()
    }

    /// Restore previous authenticated session from secure Keychain storage.
    public func restoreSession() {
        if let storedUID = keychain.read(key: keyUID), !storedUID.isEmpty,
           let storedToken = keychain.read(key: keyToken), !storedToken.isEmpty {
            self.userId = storedUID
            self.userEmail = keychain.read(key: keyEmail) ?? ""
            self.projectId = keychain.read(key: keyProject) ?? "jarvis-local-dev"
            self.isAuthenticated = true
        } else {
            self.isAuthenticated = false
            self.userId = ""
            self.userEmail = ""
        }
    }

    /// Authenticate with Firebase using email and password.
    public func signIn(email: String, pass: String) async -> Bool {
        guard !email.trimmingCharacters(in: .whitespaces).isEmpty, !pass.isEmpty else {
            self.errorMessage = "E-posta ve parola boş bırakılamaz."
            return false
        }

        self.isLoading = true
        self.errorMessage = nil

        // Genuine Firebase Authentication request
        do {
            let session = try await FirebaseAuthService.shared.authenticate(
                email: email,
                password: pass,
                projectId: self.projectId
            )
            // Save to secure Keychain storage
            keychain.save(key: keyUID, value: session.uid)
            keychain.save(key: keyEmail, value: session.email)
            keychain.save(key: keyToken, value: session.idToken)
            keychain.save(key: keyProject, value: self.projectId)

            self.userId = session.uid
            self.userEmail = session.email
            self.isAuthenticated = true
            self.isLoading = false
            return true
        } catch {
            self.isLoading = false
            self.errorMessage = error.localizedDescription
            return false
        }
    }

    /// Sign out and purge all authentication tokens from Keychain.
    public func signOut() {
        keychain.delete(key: keyUID)
        keychain.delete(key: keyEmail)
        keychain.delete(key: keyToken)
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
}
