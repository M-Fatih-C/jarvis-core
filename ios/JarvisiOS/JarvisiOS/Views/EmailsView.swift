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
                    VStack(alignment: .leading, spacing: 8) {
                        HStack {
                            Circle()
                                .fill(Color.red)
                                .frame(width: 8, height: 8)
                            Text("Öğrenci İşleri Dairesi")
                                .font(.headline)
                            Spacer()
                            Text("10:15")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }

                        Text("Ders Kayıtları ve Harç Ödemeleri Hakkında")
                            .font(.subheadline)
                            .bold()

                        Text("2026-2027 Güz dönemi ders kayıtlarının 8 Ekim'e kadar tamamlanması gerektiği bildirildi.")
                            .font(.caption)
                            .foregroundColor(.secondary)

                        HStack {
                            Label("Eğitim", systemImage: "graduationcap.fill")
                                .font(.caption2)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Color.blue.opacity(0.1))
                                .cornerRadius(4)

                            Label("Yüksek Öncelik", systemImage: "exclamationmark.circle.fill")
                                .font(.caption2)
                                .foregroundColor(.red)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Color.red.opacity(0.1))
                                .cornerRadius(4)

                            Spacer()

                            Text("Görev Çıkarıldı")
                                .font(.caption2)
                                .foregroundColor(.green)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
            .navigationTitle("E-posta Analizi")
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
                .font(.caption)
                .bold()
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(isSelected ? Color.blue : Color(.secondarySystemBackground))
                .foregroundColor(isSelected ? .white : .primary)
                .cornerRadius(14)
        }
    }
}
