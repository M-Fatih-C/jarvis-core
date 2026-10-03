import SwiftUI

public struct ChatView: View {
    @StateObject private var viewModel = ChatViewModel()

    public init() {}

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
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
                                        actionDigest: dig
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
                    TextField("Jarvis'e bir talimat verin...", text: $viewModel.inputText, axis: .vertical)
                        .padding(10)
                        .background(Color(.secondarySystemBackground))
                        .cornerRadius(18)
                        .lineLimit(1...4)

                    Button(action: {
                        viewModel.sendMessage()
                    }) {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.system(size: 32))
                            .foregroundColor(viewModel.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .gray : .blue)
                    }
                    .disabled(viewModel.inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isSending)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .background(Color(.systemBackground))
            }
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
