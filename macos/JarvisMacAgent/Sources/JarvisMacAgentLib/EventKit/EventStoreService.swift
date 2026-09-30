import Foundation
import EventKit

public protocol EventStoreServiceProtocol: Sendable {
    var eventStore: EKEventStore { get }
}

public final class EventStoreService: EventStoreServiceProtocol, @unchecked Sendable {
    public static let shared = EventStoreService()

    public let eventStore: EKEventStore
    private var observer: NSObjectProtocol?
    public var onStoreChanged: (@Sendable () -> Void)?

    public init(eventStore: EKEventStore = EKEventStore()) {
        self.eventStore = eventStore
        self.setupObserver()
    }

    deinit {
        if let observer = observer {
            NotificationCenter.default.removeObserver(observer)
        }
    }

    private func setupObserver() {
        observer = NotificationCenter.default.addObserver(
            forName: .EKEventStoreChanged,
            object: eventStore,
            queue: nil
        ) { [weak self] _ in
            self?.onStoreChanged?()
        }
    }
}
