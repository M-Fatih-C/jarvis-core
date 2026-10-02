import Foundation
import SwiftUI

@MainActor
public final class AuthViewModel: ObservableObject {
    @Published public var userEmail: String = "muhammedfatihcetintas54@gmail.com"
    @Published public var userId: String = "user_fatih_01"
    @Published public var isAuthenticated: Bool = true
    @Published public var serverURL: String = ""
    @Published public var isSaved: Bool = false

    public init() {
        self.serverURL = UserDefaults.standard.string(forKey: "jarvis_server_url") ?? "http://127.0.0.1:8765"
    }

    public func saveSettings() {
        UserDefaults.standard.set(serverURL, forKey: "jarvis_server_url")
        isSaved = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
            self.isSaved = false
        }
    }

    public func signOut() {
        isAuthenticated = false
    }
}
