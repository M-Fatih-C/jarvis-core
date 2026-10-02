import SwiftUI

public struct ApprovalsView: View {
    @StateObject private var viewModel = ApprovalsViewModel()

    public init() {}

    public var body: some View {
        NavigationStack {
            List {
                if viewModel.isLoading && viewModel.proposals.isEmpty {
                    Section {
                        HStack {
                            Spacer()
                            ProgressView("Görevler ve onaylar yükleniyor...")
                            Spacer()
                        }
                    }
                }

                if let err = viewModel.errorMessage {
                    Section {
                        Text("Hata: \(err)")
                            .foregroundColor(.red)
                            .font(.caption)
                    }
                }

                if viewModel.proposals.isEmpty && !viewModel.isLoading {
                    Section {
                        VStack(spacing: 8) {
                            Image(systemName: "checkmark.seal.fill")
                                .font(.system(size: 44))
                                .foregroundColor(.green)
                            Text("Bekleyen Onay Yok")
                                .font(.headline)
                            Text("Tüm görev önerileri ve takvim eylemleri güncel.")
                                .font(.subheadline)
                                .foregroundColor(.secondary)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 24)
                    }
                }

                ForEach(viewModel.proposals) { proposal in
                    Section(header: Text(proposal.title).font(.headline)) {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(proposal.description)
                                .font(.subheadline)
                                .foregroundColor(.secondary)

                            HStack {
                                Label(proposal.category, systemImage: "folder")
                                    .font(.caption)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(Color.blue.opacity(0.1))
                                    .cornerRadius(6)

                                Label("Öncelik: \(proposal.priority)", systemImage: "flag")
                                    .font(.caption)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(Color.orange.opacity(0.1))
                                    .cornerRadius(6)

                                Spacer()

                                Text(proposal.status.rawValue)
                                    .font(.caption)
                                    .bold()
                                    .foregroundColor(statusColor(proposal.status))
                            }

                            if let dl = proposal.deadline {
                                HStack {
                                    Image(systemName: "clock")
                                        .foregroundColor(.red)
                                    Text("Son Tarih: \(dl)")
                                        .font(.caption)
                                        .foregroundColor(.red)
                                }
                            }
                        }
                        .padding(.vertical, 4)

                        // Actions under this proposal
                        if let detail = viewModel.detailedTasks[proposal.task_id], !detail.actions.isEmpty {
                            ForEach(detail.actions) { action in
                                ActionRow(
                                    action: action,
                                    isProcessing: viewModel.processingActionId == action.action_id,
                                    onApprove: {
                                        Task {
                                            await viewModel.approveAction(actionId: action.action_id, taskId: proposal.task_id)
                                        }
                                    },
                                    onDismiss: {
                                        Task {
                                            await viewModel.dismissAction(actionId: action.action_id, taskId: proposal.task_id)
                                        }
                                    },
                                    onRequestApproval: {
                                        Task {
                                            await viewModel.requestApproval(actionId: action.action_id, taskId: proposal.task_id)
                                        }
                                    }
                                )
                            }
                        } else {
                            // Buttons to propose actions if none planned yet
                            HStack(spacing: 12) {
                                Button("Hatırlatıcı Öner") {
                                    Task {
                                        await viewModel.planReminder(taskId: proposal.task_id)
                                    }
                                }
                                .buttonStyle(.bordered)
                                .font(.caption)

                                Button("Çalışma Bloğu Planla") {
                                    Task {
                                        await viewModel.planWorkBlock(taskId: proposal.task_id)
                                    }
                                }
                                .buttonStyle(.borderedProminent)
                                .font(.caption)
                            }
                            .padding(.vertical, 4)
                        }
                    }
                }
            }
            .navigationTitle("İşlem Onayları")
            .refreshable {
                await viewModel.loadData()
            }
        }
    }

    private func statusColor(_ status: TaskProposalStatusDTO) -> Color {
        switch status {
        case .proposed: return .blue
        case .waitingApproval: return .orange
        case .approved: return .indigo
        case .executing: return .purple
        case .executed: return .green
        case .dismissed: return .gray
        case .failed: return .red
        }
    }
}

struct ActionRow: View {
    let action: TaskActionDTO
    let isProcessing: Bool
    let onApprove: () -> Void
    let onDismiss: () -> Void
    let onRequestApproval: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Image(systemName: action.action_type == .reminder ? "bell.badge.fill" : "calendar.badge.clock")
                    .foregroundColor(action.action_type == .reminder ? .orange : .blue)
                Text(action.title)
                    .font(.subheadline)
                    .bold()
                Spacer()
                Text(action.status.rawValue)
                    .font(.caption2)
                    .bold()
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.secondary.opacity(0.1))
                    .cornerRadius(4)
            }

            if let digest = action.action_digest {
                HStack(spacing: 4) {
                    Image(systemName: "lock.shield.fill")
                        .font(.caption2)
                        .foregroundColor(.green)
                    Text("Digest: \(String(digest.prefix(16)))...")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }

            if let due = action.due_date {
                Text("Bitiş / Hatırlatıcı: \(due)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            if let start = action.start_time, let end = action.end_time {
                Text("Zaman Aralığı: \(start) - \(end)")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            if action.status == .proposed {
                Button(action: onRequestApproval) {
                    if isProcessing {
                        ProgressView()
                    } else {
                        Text("Onay İste (PolicyEngine R2)")
                            .font(.caption)
                            .frame(maxWidth: .infinity)
                    }
                }
                .buttonStyle(.bordered)
            } else if action.status == .waitingApproval {
                HStack(spacing: 12) {
                    Button(role: .destructive, action: onDismiss) {
                        Text("Reddet")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .disabled(isProcessing)

                    Button(action: onApprove) {
                        if isProcessing {
                            ProgressView()
                                .frame(maxWidth: .infinity)
                        } else {
                            Text("Onayla & Kaydet")
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(isProcessing)
                }
                .font(.caption)
            } else if action.status == .executed {
                HStack {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                    Text("Apple EventKit üzerinde kaydedildi")
                        .font(.caption)
                        .foregroundColor(.green)
                }
            }
        }
        .padding(.vertical, 4)
    }
}
