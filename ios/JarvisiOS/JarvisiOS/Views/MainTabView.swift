import SwiftUI

public struct MainTabView: View {

    public init() {}

    public var body: some View {
        TabView {
            ChatView()
                .tabItem {
                    Label("Sohbet", systemImage: "bubble.left.and.bubble.right.fill")
                }

            ApprovalsView()
                .tabItem {
                    Label("Onaylar", systemImage: "checkmark.seal.fill")
                }

            CalendarScheduleView()
                .tabItem {
                    Label("Takvim", systemImage: "calendar")
                }

            EmailsView()
                .tabItem {
                    Label("E-postalar", systemImage: "envelope.fill")
                }

            SettingsView()
                .tabItem {
                    Label("Ayarlar", systemImage: "gearshape.fill")
                }
        }
    }
}
