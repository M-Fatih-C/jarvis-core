import SwiftUI

public struct SettingsView: View {
    @StateObject private var viewModel = AuthViewModel()
    @State private var emailInput: String = ""
    @State private var passwordInput: String = ""

    public init() {}

    public var body: some View {
        NavigationStack {
            Form {
                if viewModel.isAuthenticated {
                    Section(header: Text("Kullanıcı Hesabı (Firebase)")) {
                        HStack {
                            Image(systemName: "person.crop.circle.fill")
                                .font(.system(size: 36))
                                .foregroundColor(.blue)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(viewModel.userEmail.isEmpty ? "Giriş Yapıldı" : viewModel.userEmail)
                                    .font(.headline)
                                Text("UID: \(viewModel.userId)")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .padding(.vertical, 4)

                        HStack {
                            Text("Firebase Projesi")
                            Spacer()
                            Text(viewModel.projectId)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    Section(header: Text("Apple Geliştirici & İmza Durumu")) {
                        HStack {
                            Text("Ekip Türü")
                            Spacer()
                            Text("Apple Personal Team")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }

                        HStack {
                            Text("İmza Geçerlilik Süresi")
                            Spacer()
                            Text("7 Gün (Otomatik Yenilemeli)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    Section(header: Text("Hakkında")) {
                        HStack {
                            Text("Uygulama Sürümü")
                            Spacer()
                            Text("Jarvis iOS 1.1 (Milestone 5.1)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }

                        HStack {
                            Text("Platform")
                            Spacer()
                            Text("Native SwiftUI / iOS 17+")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    Section {
                        Button(role: .destructive, action: {
                            viewModel.signOut()
                        }) {
                            HStack {
                                Spacer()
                                Text("Oturumu Kapat")
                                Spacer()
                            }
                        }
                    }
                } else {
                    Section(header: Text("Giriş Yap (Firebase)")) {
                        TextField("E-posta adresi", text: $emailInput)
                            .keyboardType(.emailAddress)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)

                        SecureField("Parola", text: $passwordInput)

                        TextField("Firebase Proje ID", text: $viewModel.projectId)
                            .autocapitalization(.none)
                            .disableAutocorrection(true)

                        if let err = viewModel.errorMessage {
                            Text(err)
                                .font(.caption)
                                .foregroundColor(.red)
                        }

                        Button(action: {
                            Task {
                                _ = await viewModel.signIn(email: emailInput, pass: passwordInput)
                            }
                        }) {
                            HStack {
                                Spacer()
                                if viewModel.isLoading {
                                    ProgressView()
                                } else {
                                    Text("Giriş Yap")
                                }
                                Spacer()
                            }
                        }
                        .disabled(viewModel.isLoading || emailInput.isEmpty || passwordInput.isEmpty)
                    }
                }
            }
            .navigationTitle("Ayarlar")
        }
    }
}
