import SwiftUI

@main
struct JarvisiOSApp: App {
    @StateObject private var auth = AuthViewModel()
    var body: some Scene {
        WindowGroup {
            MainTabView()
                .environmentObject(auth)
                .preferredColorScheme(.dark)
        }
    }
}
