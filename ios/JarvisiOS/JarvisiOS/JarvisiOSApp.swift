import SwiftUI

@main
struct JarvisiOSApp: App {
    @StateObject private var auth = AuthViewModel()
    init() { JarvisShortcuts.updateAppShortcutParameters() }
    var body: some Scene {
        WindowGroup {
            SessionGateView()
                .environmentObject(auth)
                .preferredColorScheme(.dark)
        }
    }
}
