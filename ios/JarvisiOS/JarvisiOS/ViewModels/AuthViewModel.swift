import Foundation
import SwiftUI
import LocalAuthentication

@MainActor
public final class AuthViewModel: ObservableObject {
    @Published public private(set) var isAuthenticated = false
    @Published public private(set) var userId = ""
    @Published public private(set) var userEmail = ""
    @Published public private(set) var projectId = ""
    @Published public private(set) var hasSavedSession = false
    @Published public private(set) var isLoading = false
    @Published public var errorMessage: String?
    @Published public private(set) var connectionNotice: String?
    private let service: FirebaseAuthService
    private let authenticator: LocalAuthenticating
    private var autoAttempted = false
    private var epoch = UUID()
    private var validation: Task<Void, Never>?

    public convenience init() { self.init(service: .shared, authenticator: DeviceOwnerAuthenticator()) }
    init(service: FirebaseAuthService, authenticator: LocalAuthenticating) {
        self.service = service; self.authenticator = authenticator
        hasSavedSession = service.hasSavedSession
        service.onInvalidSession = { [weak self] in
            self?.resetPresentation()
            self?.hasSavedSession = service.hasSavedSession
            self?.errorMessage = "Oturumun artık geçerli değil. Lütfen tekrar giriş yap."
        }
    }

    func becameActive() async {
        guard !isLoading, !isAuthenticated, hasSavedSession, !autoAttempted else { return }
        autoAttempted = true
        await unlock()
    }

    func unlock() async {
        guard !isLoading else { return }
        isLoading = true; errorMessage = nil
        let operation = epoch
        defer { if operation == epoch { isLoading = false } }
        do {
            let context = try await authenticator.authorize()
            guard operation == epoch else { context.invalidate(); return }
            try service.unlock(context: context)
            revealSession()
        } catch {
            guard operation == epoch else { return }
            errorMessage = localMessage(error)
        }
    }

    public func signIn(email: String, pass: String, createAccount: Bool = false) async -> Bool {
        guard !isLoading, !email.trimmingCharacters(in: .whitespaces).isEmpty, !pass.isEmpty else { return false }
        isLoading = true; errorMessage = nil
        let operation = epoch
        defer { if operation == epoch { isLoading = false } }
        do {
            // First authorize secure storage; an interrupted Face ID prompt must not
            // submit credentials or replace the user's existing account.
            let context = try await authenticator.authorize()
            guard operation == epoch else { context.invalidate(); return false }
            let auth = try await service.authenticate(email: email.trimmingCharacters(in: .whitespaces), password: pass)
            guard operation == epoch else { context.invalidate(); return false }
            try service.accept(auth, context: context)
            revealSession()
            return true
        } catch {
            guard operation == epoch else { return false }
            errorMessage = localMessage(error)
            return false
        }
    }

    private func revealSession() {
        userId = service.currentUserID ?? ""
        userEmail = service.currentEmail
        projectId = service.currentProjectID ?? ""
        hasSavedSession = service.hasSavedSession
        isAuthenticated = service.isUnlocked
        connectionNotice = nil
        let operation = epoch
        validation = Task {
            do { try await service.validateAccount() }
            catch {
                guard operation == epoch, isAuthenticated else { return }
                connectionNotice = "Hesap bağlantısı şu anda doğrulanamıyor. İnternete bağlandığında tekrar denenecek."
            }
        }
    }

    func enteredBackground() {
        epoch = UUID()
        validation?.cancel(); validation = nil
        authenticator.cancel()
        service.lock()
        resetPresentation()
        hasSavedSession = service.hasSavedSession
        autoAttempted = false
    }

    public func signOut() {
        epoch = UUID()
        validation?.cancel(); validation = nil
        authenticator.cancel()
        resetPresentation()
        do {
            try service.signOut()
            hasSavedSession = false
            errorMessage = nil
        } catch {
            hasSavedSession = service.hasSavedSession
            errorMessage = "Oturum kilitlendi ancak kayıtlı bilgiler temizlenemedi. Tekrar dene."
        }
        autoAttempted = true
    }

    private func resetPresentation() {
        isAuthenticated = false; isLoading = false
        userId = ""; userEmail = ""; projectId = ""
        connectionNotice = nil
    }

    private func localMessage(_ error: Error) -> String {
        if let error = error as? LAError {
            switch error.code {
            case .userCancel, .appCancel, .systemCancel: return "Oturum kilitli. Hazır olduğunda tekrar deneyebilirsin."
            case .passcodeNotSet: return "Güvenli giriş için iPhone Ayarlar bölümünden bir cihaz parolası belirle."
            case .biometryLockout: return "Face ID geçici olarak kilitli. Cihaz parolanla tekrar dene."
            default: return "Kimliğin doğrulanamadı. Face ID veya cihaz parolasıyla tekrar dene."
            }
        }
        return error.localizedDescription
    }
}
