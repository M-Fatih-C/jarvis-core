import SwiftUI

public struct EmailsView: View {
    @StateObject private var viewModel = EmailsViewModel()

    public init() {}

    public var body: some View {
        NavigationStack {
            List {
                Section(header: Text("Kategori Filtresi")) {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            CategoryChip(title: "Tümü", isSelected: viewModel.selectedCategory == "ALL") {
                                viewModel.selectedCategory = "ALL"
                            }
                            CategoryChip(title: "Eğitim", isSelected: viewModel.selectedCategory == "education") {
                                viewModel.selectedCategory = "education"
                            }
                            CategoryChip(title: "Kariyer", isSelected: viewModel.selectedCategory == "work_career") {
                                viewModel.selectedCategory = "work_career"
                            }
                            CategoryChip(title: "Finans", isSelected: viewModel.selectedCategory == "finance") {
                                viewModel.selectedCategory = "finance"
                            }
                            CategoryChip(title: "Toplantı", isSelected: viewModel.selectedCategory == "meetings") {
                                viewModel.selectedCategory = "meetings"
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }

                Section(header: Text("Önemli E-posta Analizleri (Qwen AI)")) {
                    if viewModel.isLoading {
                        HStack {
                            Spacer()
                            ProgressView("E-posta analizleri sorgulanıyor...")
                            Spacer()
                        }
                        .padding(.vertical, 12)
                    } else if viewModel.filteredEmails.isEmpty {
                        VStack(spacing: 8) {
                            Image(systemName: "tray")
                                .font(.largeTitle)
                                .foregroundColor(.secondary)
                            Text("İncelenmiş e-posta bulunmuyor veya sunucu bağlantısı bekleniyor.")
                                .font(.subheadline)
                                .foregroundColor(.secondary)
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 16)
                    } else {
                        ForEach(viewModel.filteredEmails) { item in
                            VStack(alignment: .leading, spacing: 6) {
                                HStack {
                                    Circle()
                                        .fill(item.is_important ? Color.red : Color.gray)
                                        .frame(width: 8, height: 8)
                                    Text(item.sender)
                                        .font(.headline)
                                    Spacer()
                                    Text(item.received_at)
                                        .font(.caption2)
                                        .foregroundColor(.secondary)
                                }

                                Text(item.subject)
                                    .font(.subheadline)
                                    .bold()

                                Text(item.summary)
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                    .lineLimit(3)

                                HStack {
                                    Label(item.category ?? "Genel", systemImage: "tag.fill")
                                        .font(.caption2)
                                        .padding(.horizontal, 6)
                                        .padding(.vertical, 2)
                                        .background(Color.blue.opacity(0.1))
                                        .cornerRadius(6)

                                    if item.has_actionable_task {
                                        Label("Görev Çıkarıldı", systemImage: "calendar.badge.plus")
                                            .font(.caption2)
                                            .foregroundColor(.green)
                                    }
                                }
                                .padding(.top, 2)
                            }
                            .padding(.vertical, 4)
                        }
                    }
                }
            }
            .navigationTitle("E-posta Analizi")
            .refreshable {
                await viewModel.loadEmails()
            }
        }
    }
}

struct CategoryChip: View {
    let title: String
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.caption.bold())
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(isSelected ? Color.blue : Color(.secondarySystemBackground))
                .foregroundColor(isSelected ? .white : .primary)
                .cornerRadius(14)
        }
    }
}
