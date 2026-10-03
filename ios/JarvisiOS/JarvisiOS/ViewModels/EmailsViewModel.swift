import Foundation
import SwiftUI

@MainActor
public final class EmailsViewModel: ObservableObject {
    @Published public var emails: [iOSEmailItemDTO] = []
    @Published public var selectedCategory: String = "ALL"
    @Published public var isLoading: Bool = false
    @Published public var errorMessage: String? = nil

    private let apiService = JarvisAPIService.shared

    public var filteredEmails: [iOSEmailItemDTO] {
        if selectedCategory == "ALL" {
            return emails
        }
        return emails.filter { ($0.category ?? "").lowercased() == selectedCategory.lowercased() }
    }

    public init() {
        Task {
            await loadEmails()
        }
    }

    public func loadEmails() async {
        isLoading = true
        errorMessage = nil
        do {
            let fetched = try await apiService.fetchEmails()
            self.emails = fetched
        } catch {
            self.emails = []
            self.errorMessage = "E-posta özetleri alınamadı: \(error.localizedDescription)"
        }
        isLoading = false
    }
}
