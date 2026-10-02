import SwiftUI

public struct ChatView: View {
    @StateObject private var viewModel = ChatViewModel()

    public init() {}

    public var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Message list
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            ForEach(viewModel.messages) { msg in
                                ChatBubble(message: msg)
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

                if viewModel.isSending {
                    HStack(spacing: 8) {
                        ProgressView()
                            .scaleEffect(0.8)
                        Text("Jarvis Mac mini üzerinde düşünüyor...")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .padding(.vertical, 8)
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

    var body: some View {
        HStack {
            if message.sender == .user {
                Spacer()
            }

            VStack(alignment: message.sender == .user ? .trailing : .leading, spacing: 4) {
                Text(message.text)
                    .font(.body)
                    .foregroundColor(message.sender == .user ? .white : .primary)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(message.sender == .user ? Color.blue : Color(.secondarySystemBackground))
                    .cornerRadius(16)

                Text(message.timestamp, style: .time)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .padding(.horizontal, 4)
            }

            if message.sender == .jarvis {
                Spacer()
            }
        }
    }
}
