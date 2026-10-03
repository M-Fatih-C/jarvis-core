import SwiftUI
import UIKit

struct SessionGateView: View {
    @EnvironmentObject private var auth: AuthViewModel
    @Environment(\.scenePhase) private var phase
    @State private var showPassword = false
    @State private var email = ""
    @State private var password = ""

    var body: some View {
        ZStack {
            if auth.isAuthenticated {
                MainTabView()
                    .safeAreaInset(edge: .top) {
                        if let notice = auth.connectionNotice {
                            Text(notice).font(.caption).padding(8).frame(maxWidth: .infinity).background(.orange.opacity(0.18))
                        }
                    }
            } else {
                ScrollView {
                    VStack(spacing: 22) {
                        Image("JarvisLogo").resizable().scaledToFit().frame(width: 170, height: 170)
                            .clipShape(RoundedRectangle(cornerRadius: 36)).padding(.top, 55)
                        Text("JARVIS").font(.largeTitle.weight(.semibold)).tracking(4)
                        Text(auth.hasSavedSession ? "Tekrar hoş geldin, patron." : "Kişisel asistanına güvenle bağlan.")
                            .foregroundStyle(.secondary)
                        if auth.isLoading { ProgressView("Güvenli oturum açılıyor…") }
                        if let error = auth.errorMessage {
                            Text(error).font(.subheadline).foregroundStyle(.orange).multilineTextAlignment(.center)
                        }
                        if auth.hasSavedSession && !showPassword {
                            Button { Task { await auth.unlock() } } label: {
                                Label("Face ID ile aç", systemImage: "faceid").frame(maxWidth: .infinity, minHeight: 48)
                            }.buttonStyle(.borderedProminent).tint(.orange).foregroundStyle(.black)
                            Text("Gerektiğinde iPhone cihaz parolanı kullanabilirsin.").font(.caption).foregroundStyle(.secondary)
                            Button("Hesabımla yeniden giriş yap") { showPassword = true }
                        } else {
                            TextField("E-posta", text: $email).keyboardType(.emailAddress).textContentType(.username)
                                .textInputAutocapitalization(.never).autocorrectionDisabled()
                            SecureField("Parola", text: $password).textContentType(.password)
                            Button("Giriş yap") {
                                Task { if await auth.signIn(email: email, pass: password) { password = ""; showPassword = false } }
                            }.buttonStyle(.borderedProminent).frame(minHeight: 48)
                                .disabled(email.isEmpty || password.isEmpty)
                            if auth.hasSavedSession { Button("Face ID’ye dön") { showPassword = false; password = "" } }
                        }
                    }.textFieldStyle(.roundedBorder).padding(30).disabled(auth.isLoading)
                }.background(Color(.systemBackground))
            }
            if phase != .active {
                Color(.systemBackground).ignoresSafeArea()
                Image("JarvisLogo").resizable().scaledToFit().frame(width: 130, height: 130)
                    .clipShape(RoundedRectangle(cornerRadius: 30))
            }
        }
        .background(PrivacyShield())
        .task { if phase == .active { await auth.becameActive() } }
        .onChange(of: phase) { phase in
            if phase == .background { password = ""; auth.enteredBackground() }
            if phase == .active { Task { await auth.becameActive() } }
        }
    }
}

/// Install an opaque UIKit cover synchronously, before the app-switcher snapshot.
private struct PrivacyShield: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> Controller { Controller() }
    func updateUIViewController(_ controller: Controller, context: Context) {}
    final class Controller: UIViewController {
        private var cover: UIView?
        override func viewDidLoad() {
            super.viewDidLoad()
            NotificationCenter.default.addObserver(self, selector: #selector(hideContent), name: UIApplication.willResignActiveNotification, object: nil)
            NotificationCenter.default.addObserver(self, selector: #selector(revealContent), name: UIApplication.didBecomeActiveNotification, object: nil)
        }
        @objc private func hideContent() {
            guard cover == nil, let window = view.window else { return }
            let shield = UIView(frame: window.bounds)
            shield.backgroundColor = .black
            shield.autoresizingMask = [.flexibleWidth, .flexibleHeight]
            let logo = UIImageView(image: UIImage(named: "JarvisLogo"))
            logo.contentMode = .scaleAspectFit
            logo.frame = CGRect(x: (window.bounds.width-130)/2, y: (window.bounds.height-130)/2, width: 130, height: 130)
            logo.autoresizingMask = [.flexibleLeftMargin, .flexibleRightMargin, .flexibleTopMargin, .flexibleBottomMargin]
            shield.addSubview(logo); window.addSubview(shield); cover = shield
        }
        @objc private func revealContent() { cover?.removeFromSuperview(); cover = nil }
        deinit { NotificationCenter.default.removeObserver(self); cover?.removeFromSuperview() }
    }
}
