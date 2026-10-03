import SwiftUI

@main
struct JarvisiOSApp: App {
    @StateObject private var auth = AuthViewModel()
    init() { JarvisShortcuts.updateAppShortcutParameters() }
    var body: some Scene {
        WindowGroup {
            MainTabView()
                .environmentObject(auth)
                .preferredColorScheme(.dark)
        }
    }
}
