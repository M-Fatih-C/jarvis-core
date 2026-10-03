import SwiftUI

public struct ChatView: View {
    @EnvironmentObject private var auth: AuthViewModel
    @StateObject private var viewModel = ChatViewModel()
    @StateObject private var voice = VoiceConversationService()
    @StateObject private var health = DeviceStatusViewModel()
    @Environment(\.scenePhase) private var scenePhase
    @FocusState private var composerFocused: Bool
    @State private var showVoice = false
    @State private var startingVoice = false
    @State private var voiceTurn = false
    @State private var showSignIn = false

    public init() {}

    private var avatarState: JarvisVoiceState {
        if voice.state == .error { return .error }
        if !auth.isAuthenticated || !health.isConnected { return .disconnected }
        return viewModel.isSending ? .thinking : voice.state
    }
    private var canSend: Bool {
        auth.isAuthenticated && !viewModel.isSending && voice.state != .listening &&
        !viewModel.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
    private var statusTitle: String {
        if !auth.isAuthenticated { return "Giriş yaparak başla" }
        if health.isChecking && !health.isConnected { return "Bağlanıyor…" }
        if !health.isConnected { return "Mac bağlantısı bekleniyor" }
        if viewModel.messages.last?.status == .waitingApproval { return "Onayın bekleniyor" }
        if viewModel.isSending { return "Yanıt hazırlanıyor…" }
        return voice.state == .idle ? "Hazır" : voice.state.title
    }

    public var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 20) {
                        if viewModel.messages.count <= 1 { welcome }
                        ForEach(viewModel.messages) { message in
                            ChatBubble(message: message, onCheckStatus: { checkPending() }) { id, decision, digest in
                                viewModel.respondToApproval(messageId: message.id, approvalId: id,
                                    decision: decision, actionDigest: digest,
                                    userId: auth.userId, projectId: auth.projectId)
                            }
                        }
                        Color.clear.frame(height: 1).id("chat-bottom")
                    }
                    .padding(.horizontal, 18)
                    .padding(.vertical, 20)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: viewModel.messages) { _ in
                    withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("chat-bottom", anchor: .bottom) }
                }
                .onChange(of: composerFocused) { focused in
                    if focused {
                        Task { @MainActor in
                            try? await Task.sleep(nanoseconds: 300_000_000)
                            withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("chat-bottom", anchor: .bottom) }
                        }
                    }
                }
            }
            .background(Color(.systemBackground))
            .safeAreaInset(edge: .bottom, spacing: 0) { composer }
            .navigationTitle("Jarvis")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    VStack(spacing: 3) {
                        Text("Jarvis").font(.headline)
                        HStack(spacing: 5) {
                            Circle().fill(health.isConnected ? Color.mint : Color.secondary).frame(width: 5, height: 5)
                            Text(statusTitle).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    .accessibilityElement(children: .combine)
                }
                ToolbarItem(placement: .topBarTrailing) {
                    if composerFocused {
                        Button { composerFocused = false } label: {
                            Image(systemName: "keyboard.chevron.compact.down").frame(width: 44, height: 44)
                        }.accessibilityLabel("Klavyeyi kapat")
                    } else {
                        Menu {
                            Toggle("Eller serbest sohbet", isOn: $voice.conversationMode)
                                .disabled(!auth.isAuthenticated)
                            Button("Bağlantıyı kontrol et", systemImage: "arrow.clockwise") { checkPending() }
                            if voice.state == .speaking || voice.state == .listening {
                                Button("Sesi durdur", systemImage: "stop.fill") { stopVoice() }
                            }
                        } label: {
                            Image(systemName: "ellipsis.circle").frame(width: 44, height: 44)
                        }.accessibilityLabel("Sohbet seçenekleri")
                    }
                }
            }
            .sheet(isPresented: $showSignIn) { SettingsView() }
            .sheet(isPresented: $showVoice, onDismiss: stopVoice) { voicePanel }
            .onAppear { connectVoice() }
            .task(id: auth.isAuthenticated) {
                if auth.isAuthenticated { await viewModel.resumePending(userId: auth.userId, projectId: auth.projectId) }
            }
            .task(id: auth.isAuthenticated) {
                guard auth.isAuthenticated else { return }
                while !Task.isCancelled {
                    if scenePhase == .active { await health.checkStatus() }
                    do { try await Task.sleep(nanoseconds: 20_000_000_000) } catch { break }
                }
            }
            .onChange(of: voice.conversationMode) { enabled in
                if enabled && auth.isAuthenticated && !viewModel.isSending {
                    composerFocused = false
                    Task { await voice.startListening() }
                } else if !enabled { voice.stop() }
            }
            .onChange(of: scenePhase) { phase in
                if phase != .active { stopVoice() }
                else if auth.isAuthenticated { checkPending() }
            }
            .onChange(of: auth.userId) { _ in stopVoice() }
            .onDisappear { stopVoice() }
        }
    }

    private var welcome: some View {
        VStack(spacing: 14) {
            JarvisAvatarView(state: avatarState, level: voice.level, diameter: 104, showsState: false)
            Text("Bugün ne yapalım?").font(.title2.weight(.semibold))
            Text("Bir soru sor, gününü planla ya da konuşmaya başla.")
                .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
            HStack(spacing: 8) {
                suggestion("Bugünkü programım", symbol: "calendar", prompt: "Bugünkü programım nedir?")
                suggestion("Beni neler bekliyor?", symbol: "sparkles", prompt: "Bu haftaki programım nedir?")
            }.padding(.top, 4)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 12)
    }

    private func suggestion(_ title: String, symbol: String, prompt: String) -> some View {
        Button {
            viewModel.inputText = prompt
            composerFocused = true
        } label: {
            Label(title, systemImage: symbol)
                .font(.caption.weight(.medium)).padding(12)
                .frame(maxWidth: .infinity, minHeight: 44)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14))
        }.buttonStyle(.plain)
    }

    private var composer: some View {
        VStack(spacing: 8) {
            if voice.state == .listening || voice.state == .speaking {
                HStack(spacing: 10) {
                    JarvisAvatarView(state: voice.state, level: voice.level, diameter: 64, showsState: false)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(voice.state.title).font(.subheadline.weight(.semibold))
                        Text(voice.state == .listening ? (voice.transcript.isEmpty ? "Seni dinliyorum…" : voice.transcript) : "Konuşmayı kesmek için mikrofona bas.")
                            .font(.caption).foregroundStyle(.secondary).lineLimit(3)
                    }.frame(maxWidth: .infinity, alignment: .leading)
                    Button(action: stopVoice) {
                        Image(systemName: "stop.fill").frame(width: 44, height: 44)
                    }.accessibilityLabel("Sesi durdur")
                }
            }
            if let error = voice.errorMessage {
                Text(error).font(.caption).foregroundStyle(.orange).frame(maxWidth: .infinity, alignment: .leading)
            }
            if !auth.isAuthenticated {
                Button { showSignIn = true } label: {
                    Label("Jarvis’e bağlanmak için giriş yap", systemImage: "person.crop.circle")
                        .font(.subheadline.weight(.medium)).frame(maxWidth: .infinity, minHeight: 44)
                }.buttonStyle(.bordered)
            }
            HStack(alignment: .bottom, spacing: 8) {
                microphone
                TextField("Mesaj yaz…", text: $viewModel.inputText, axis: .vertical)
                    .font(.body).lineLimit(1...5).padding(.vertical, 12)
                    .focused($composerFocused)
                    .accessibilityLabel("Mesaj")
                Button(action: sendText) {
                    Image(systemName: "arrow.up")
                        .font(.system(size: 18, weight: .semibold))
                        .foregroundStyle(canSend ? Color.black : Color.secondary)
                        .frame(width: 44, height: 44)
                        .background(canSend ? Color.mint : Color(.tertiarySystemBackground), in: Circle())
                }
                .disabled(!canSend).accessibilityLabel("Mesajı gönder")
            }
            .padding(6)
            .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 26))
            .overlay(RoundedRectangle(cornerRadius: 26).stroke(composerFocused ? Color.mint.opacity(0.45) : Color.white.opacity(0.08)))
            if !composerFocused {
                Text(voice.conversationMode ? "Eller serbest açık · Sessiz kaldığında gönderilir" : "Konuşmak için mikrofona dokun")
                    .font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 14).padding(.top, 10).padding(.bottom, 8)
        .background(.bar)
    }

    private var microphone: some View {
        Button {
            guard auth.isAuthenticated else { showSignIn = true; return }
            composerFocused = false
            showVoice = true
            if !viewModel.isSending { toggleListening() }
        } label: {
            Image(systemName: voice.state == .listening ? "waveform" : "mic.fill")
                .font(.system(size: 19, weight: .medium))
                .foregroundStyle(voice.state == .listening ? Color.mint : Color.secondary)
                .frame(width: 44, height: 44)
        }.accessibilityLabel("Sesli sohbeti aç")
    }

    private var voicePanel: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 26) {
                    JarvisAvatarView(state: avatarState, level: voice.level, diameter: 260)
                        .padding(.top, 20)
                    Text(voice.state == .listening ? "Seni dinliyorum, patron." : statusTitle)
                        .font(.title2.weight(.medium)).multilineTextAlignment(.center)
                    if !voice.transcript.isEmpty {
                        Text(voice.transcript).font(.body).foregroundStyle(.secondary)
                            .multilineTextAlignment(.center).textSelection(.enabled)
                    }
                    if let error = voice.errorMessage {
                        Text(error).font(.subheadline).foregroundStyle(.orange)
                    }
                    Button(action: toggleListening) {
                        Label(voice.state == .listening ? "Bitir ve gönder" : "Konuşmaya başla",
                              systemImage: voice.state == .listening ? "stop.fill" : "mic.fill")
                            .font(.headline).frame(maxWidth: .infinity, minHeight: 60)
                    }
                    .buttonStyle(.borderedProminent).tint(.cyan).foregroundStyle(.black)
                    .disabled(viewModel.isSending || startingVoice)
                    Toggle("Eller serbest sohbet", isOn: $voice.conversationMode).tint(.cyan)
                    Text(voice.conversationMode
                         ? "Sessiz kaldığında mesajın gönderilir. Yanıt bitince yeniden dinler."
                         : "Başlatmak için dokun. Konuşman bitince tekrar dokunarak gönder.")
                        .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center)
                    if viewModel.messages.last?.status == .waitingApproval {
                        Button("Onayı sohbette incele") { showVoice = false }
                            .buttonStyle(.bordered).frame(minHeight: 44)
                    }
                }.padding(24)
            }
            .background(Color(red: 0.015, green: 0.035, blue: 0.055))
            .navigationTitle("J.A.R.V.I.S.").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .topBarTrailing) {
                Button("Kapat") { showVoice = false }
            } }
        }.preferredColorScheme(.dark)
    }

    private func toggleListening() {
        guard !viewModel.isSending, !startingVoice else { return }
        if voice.state == .listening { voice.finishListening(); return }
        startingVoice = true
        Task {
            await voice.startListening()
            startingVoice = false
            if !showVoice { voice.stop() }
        }
    }

    private func sendText() {
        guard canSend else { return }
        voiceTurn = false
        stopVoice()
        viewModel.sendMessage(userId: auth.userId, projectId: auth.projectId)
    }
    private func checkPending() {
        Task {
            await health.checkStatus()
            await viewModel.resumePending(userId: auth.userId, projectId: auth.projectId)
        }
    }
    private func stopVoice() { voiceTurn = false; voice.conversationMode = false; voice.stop() }
    private func connectVoice() {
        voice.onTurn = { text in
            voiceTurn = true
            viewModel.inputText = text
            viewModel.sendMessage(userId: auth.userId, projectId: auth.projectId)
        }
        viewModel.onResponse = { text in
            if voiceTurn { voice.speak(text); voiceTurn = false }
        }
        viewModel.onFailure = { error in voice.fail(error); voiceTurn = false }
    }
}

struct ChatBubble: View {
    let message: ChatMessageItem
    var onCheckStatus: (() -> Void)? = nil
    var onApprovalAction: ((String, String, String?) -> Void)? = nil

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            if message.sender == .user { Spacer(minLength: 36) }
            VStack(alignment: .leading, spacing: 10) {
                if message.sender == .jarvis {
                    Text("JARVIS").font(.system(size: 10, weight: .semibold)).tracking(1.5).foregroundStyle(.mint)
                }
                if message.status == .failed {
                    Label("Yanıt alınamadı", systemImage: "exclamationmark.circle").foregroundStyle(.orange)
                    DisclosureGroup("Ayrıntılar") { Text(message.text).font(.caption).textSelection(.enabled) }
                        .font(.caption).foregroundStyle(.secondary)
                    Button("İsteğin durumunu kontrol et") { onCheckStatus?() }
                        .font(.subheadline).frame(minHeight: 44)
                } else if !message.text.isEmpty && message.approvalData == nil {
                    Text(LocalizedStringKey(message.text)).font(.body).lineSpacing(4).textSelection(.enabled)
                }
                if let approval = message.approvalData { approvalCard(approval) }
                if message.status == .queued || message.status == .running {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text(message.status == .queued ? "İletiliyor…" : "Yanıt hazırlanıyor…")
                            .font(.subheadline).foregroundStyle(.secondary)
                    }.padding(.vertical, 4)
                }
                Text(message.timestamp, style: .time).font(.caption2).foregroundStyle(.tertiary)
            }
            .padding(message.sender == .user ? 14 : 0)
            .background(message.sender == .user ? Color(.secondarySystemBackground) : .clear, in: RoundedRectangle(cornerRadius: 20))
            .contextMenu {
                if !message.text.isEmpty {
                    Button("Kopyala", systemImage: "doc.on.doc") { UIPasteboard.general.string = message.text }
                }
            }
            if message.sender == .jarvis { Spacer(minLength: 12) }
        }
        .frame(maxWidth: .infinity, alignment: message.sender == .user ? .trailing : .leading)
    }

    private func approvalCard(_ approval: PendingApprovalData) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Onayın gerekiyor", systemImage: "hand.raised.fill").font(.subheadline.weight(.semibold)).foregroundStyle(.orange)
            Text(actionTitle(approval.pendingTool)).font(.headline)
            ForEach(approval.arguments.sorted(by: { $0.key < $1.key }), id: \.key) { key, value in
                VStack(alignment: .leading, spacing: 3) {
                    Text(argumentTitle(key)).font(.caption).foregroundStyle(.secondary)
                    Text(argumentValue(key, value)).font(.subheadline).textSelection(.enabled)
                }
            }
            Text("Yalnızca yukarıdaki işlem onaylandıktan sonra uygulanır.")
                .font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 10) {
                Button { onApprovalAction?(approval.approvalId, "rejected", approval.actionDigest) } label: {
                    Text("Vazgeç").frame(maxWidth: .infinity, minHeight: 28)
                }.buttonStyle(.bordered).controlSize(.large)
                Button { onApprovalAction?(approval.approvalId, "approved", approval.actionDigest) } label: {
                    Text("Onayla").frame(maxWidth: .infinity, minHeight: 28)
                }
                    .buttonStyle(.borderedProminent).tint(.mint).foregroundStyle(.black)
                    .controlSize(.large)
            }
        }
        .padding(16).frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 18))
        .overlay(RoundedRectangle(cornerRadius: 18).stroke(Color.orange.opacity(0.3)))
    }
    private func actionTitle(_ tool: String) -> String {
        ["calendar.create_event": "Takvime etkinlik ekle", "calendar.update_event": "Etkinliği güncelle",
         "calendar.delete_event": "Etkinliği sil", "reminders.create": "Hatırlatıcı ekle"][tool] ?? tool
    }
    private func argumentTitle(_ key: String) -> String {
        ["title": "Başlık", "start": "Başlangıç", "end": "Bitiş", "notes": "Notlar", "location": "Konum",
         "due_date": "Son tarih", "alarm_minutes_before": "Kaç dakika önce hatırlatılsın", "calendar_id": "Takvim", "list_id": "Liste"][key] ?? key
    }
    private func argumentValue(_ key: String, _ value: String) -> String {
        guard ["start", "end", "due_date"].contains(key) else { return value }
        let parser = ISO8601DateFormatter()
        parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        guard let date = parser.date(from: value) ?? ISO8601DateFormatter().date(from: value) else { return value }
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "tr_TR")
        formatter.timeZone = TimeZone(identifier: "Europe/Istanbul")
        formatter.dateFormat = "d MMMM yyyy, EEEE · HH:mm"
        return formatter.string(from: date) + " (Türkiye)"
    }
}
