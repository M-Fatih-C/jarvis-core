import SwiftUI

public struct ChatView: View {
    @EnvironmentObject private var auth: AuthViewModel
    @StateObject private var viewModel = ChatViewModel()
    @StateObject private var voice = VoiceConversationService()
    @StateObject private var health = DeviceStatusViewModel()
    @Environment(\.scenePhase) private var scenePhase
    @State private var microphonePressed = false
    @State private var voiceTurn = false

    public init() {}

    private var avatarState: JarvisVoiceState {
        if voice.state == .error { return .error }
        if !auth.isAuthenticated || !health.isConnected { return .disconnected }
        return viewModel.isSending ? .thinking : voice.state
    }

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                JarvisAvatarView(state: avatarState, level: voice.level)
                if !voice.transcript.isEmpty && voice.state == .listening {
                    Text(voice.transcript).font(.callout).padding(.horizontal)
                }
                if let error = voice.errorMessage {
                    Text(error).font(.caption).foregroundStyle(.orange).padding(.horizontal)
                }
                Toggle("Sohbet modu · sessizlikte gönder", isOn: $voice.conversationMode)
                    .font(.caption).padding(.horizontal)
                    .disabled(!auth.isAuthenticated)
                // Connection bar if error or status
                if viewModel.connectionState != "Bağlı" {
                    HStack {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundColor(.orange)
                        Text(viewModel.connectionState)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .padding(.vertical, 4)
                    .frame(maxWidth: .infinity)
                    .background(Color.orange.opacity(0.1))
                }

                // Message list
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            ForEach(viewModel.messages) { msg in
                                ChatBubble(message: msg) { appID, dec, dig in
                                    viewModel.respondToApproval(
                                        messageId: msg.id,
                                        approvalId: appID,
                                        decision: dec,
                                        actionDigest: dig,
                                        userId: auth.userId, projectId: auth.projectId
                                    )
                                }
                            }
                        }
                        .padding(.horizontal, 16)
                        .padding(.top, 12)
                    }
                    .onChange(of: viewModel.messages.count) { _ in
                        if let last = viewModel.messages.last {
                            withAnimation {
                                proxy.scrollTo(last.id, anchor: .bottom)
                            }
                        }
                    }
                }

                // Input Bar
                HStack(spacing: 12) {
                    Image(systemName: voice.state == .listening ? "waveform" : "mic.fill")
                        .font(.title2).foregroundStyle(voice.state == .listening ? .mint : .cyan)
                        .frame(width: 44, height: 44).contentShape(Rectangle())
                        .accessibilityLabel("Konuşmak için basılı tutun; konuşmayı kesmek için basın")
                        .accessibilityAddTraits(.isButton)
                        .accessibilityAction {
                            if voice.state == .listening { voice.finishListening() }
                            else if auth.isAuthenticated && !viewModel.isSending { Task { await voice.startListening() } }
                        }
                        .gesture(DragGesture(minimumDistance: 0)
                            .onChanged { _ in
                                guard !microphonePressed, auth.isAuthenticated, !viewModel.isSending else { return }
                                microphonePressed = true
                                Task {
                                    await voice.startListening()
                                    if !microphonePressed { voice.stop() }
                                }
                            }
                            .onEnded { _ in
                                microphonePressed = false
                                voice.finishListening()
                            })
                    TextField("Jarvis'e bir talimat verin...", text: $viewModel.inputText, axis: .vertical)
                        .padding(10)
                        .background(Color(.secondarySystemBackground))
                        .cornerRadius(18)
                        .lineLimit(1...4)

                    Button(action: {
                        viewModel.sendMessage(userId: auth.userId, projectId: auth.projectId)
                    }) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 32))
                            .foregroundColor(viewModel.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .gray : .blue)
                    }
                    .disabled(viewModel.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isSending || !auth.isAuthenticated)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(Color(.systemBackground))
            }
            .onAppear {
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
            .task(id: auth.isAuthenticated) {
                if auth.isAuthenticated { await viewModel.resumePending(userId: auth.userId, projectId: auth.projectId) }
            }
            .task(id: auth.isAuthenticated) {
                guard auth.isAuthenticated else { return }
                while !Task.isCancelled {
                    if scenePhase == .active {
                        await health.checkStatus()
                        viewModel.connectionState = health.isConnected ? "Bağlı" : "Mac bağlantısı bekleniyor"
                    }
                    do { try await Task.sleep(nanoseconds: 20_000_000_000) } catch { break }
                }
            }
            .onChange(of: voice.conversationMode) { enabled in
                if enabled && auth.isAuthenticated && !viewModel.isSending { Task { await voice.startListening() } }
                else if !enabled { voice.stop() }
            }
            .onChange(of: scenePhase) { phase in
                if phase != .active { voice.conversationMode = false; voice.stop(); microphonePressed = false }
                else if auth.isAuthenticated { Task { await viewModel.resumePending(userId: auth.userId, projectId: auth.projectId) } }
            }
            .onChange(of: auth.userId) { _ in voice.conversationMode = false; voice.stop() }
            .onDisappear { voice.conversationMode = false; voice.stop() }
            .navigationTitle("Jarvis AI")
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

struct ChatBubble: View {
    let message: ChatMessageItem
    var onApprovalAction: ((String, String, String?) -> Void)? = nil

    var body: some View {
        HStack {
            if message.sender == .user {
                Spacer()
            }

            VStack(alignment: message.sender == .user ? .trailing : .leading, spacing: 4) {
                // Main text content
                if !message.text.isEmpty {
                    Text(message.text)
                        .font(.body)
                        .foregroundColor(message.sender == .user ? .white : .primary)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 10)
                        .background(message.sender == .user ? Color.blue : Color(.secondarySystemBackground))
                        .cornerRadius(16)
                }

                // Interactive Human-in-the-Loop Approval Card
                if let approval = message.approvalData {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Image(systemName: "lock.shield.fill")
                                .foregroundColor(.orange)
                            Text("İşlem Onayı Gerekli (R2/R4)")
                                .font(.caption.bold())
                                .foregroundColor(.orange)
                        }

                        Text("Hedef Eylem: \(approval.pendingTool)")
                            .font(.subheadline.bold())

                        ForEach(approval.arguments.sorted(by: { $0.key < $1.key }), id: \.key) { k, v in
                            HStack {
                                Text("\(k):")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                Text(v)
                                    .font(.caption)
                            }
                        }

                        HStack(spacing: 12) {
                            Button("Reddet") {
                                onApprovalAction?(approval.approvalId, "rejected", approval.actionDigest)
                            }
                            .buttonStyle(.bordered)
                            .tint(.red)

                            Button("Onayla ve Yürüt") {
                                onApprovalAction?(approval.approvalId, "approved", approval.actionDigest)
                            }
                            .buttonStyle(.borderedProminent)
                            .tint(.green)
                        }
                        .padding(.top, 4)
                    }
                    .padding(12)
                    .background(Color.orange.opacity(0.1))
                    .cornerRadius(12)
                    .overlay(
                        RoundedRectangle(cornerRadius: 12)
                            .stroke(Color.orange.opacity(0.3), lineWidth: 1)
                    )
                }

                // Status Indicator
                HStack(spacing: 4) {
                    if message.status == .queued || message.status == .running {
                        ProgressView()
                            .scaleEffect(0.6)
                        Text(message.status.rawValue)
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    } else if message.status == .failed {
                        Image(systemName: "exclamationmark.circle.fill")
                            .font(.caption2)
                            .foregroundColor(.red)
                        Text(message.status.rawValue)
                            .font(.caption2)
                            .foregroundColor(.red)
                    }

                    Text(message.timestamp, style: .time)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                .padding(.horizontal, 4)
            }

            if message.sender == .jarvis {
                Spacer()
            }
        }
    }
}
