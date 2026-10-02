import Foundation
import SwiftUI

@MainActor
public final class EmailsViewModel: ObservableObject {
    @Published public var emails: [iOSEmailItemDTO] = []
    @Published public var selectedCategory: String = "ALL"
    @Published public var isLoading: Bool = false
    @Published public var errorMessage: String? = nil

    private let apiService = JarvisAPIService.shared

    public init() {
        Task {
            await loadEmails()
        }
    }

    public func loadEmails() async {
        isLoading = true
        errorMessage = nil
        // Sample seed or endpoint retrieval
        isLoading = false
    }
}
