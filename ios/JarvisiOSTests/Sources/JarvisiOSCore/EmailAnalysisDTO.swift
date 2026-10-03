import Foundation

public struct iOSEmailItemDTO: Identifiable, Codable, Sendable {
    public let message_id: String
    public let thread_id: String
    public let sender: String
    public let sender_email: String
    public let recipient: String
    public let subject: String
    public let received_at: String
    public let body_preview: String
    public let category: String?
    public let importance: String?

    public var id: String { message_id }
    public var summary: String { body_preview }
    public var is_important: Bool {
        let imp = (importance ?? "").uppercased()
        return imp == "HIGH" || imp == "CRITICAL"
    }
    public var has_actionable_task: Bool {
        let cat = (category ?? "").lowercased()
        return cat == "education" || cat == "work_career" || cat == "meeting"
    }
}

public struct DeviceRecordDTO: Codable, Sendable {
    public let device_id: String
    public let name: String
    public let type: String
    public let status: String
    public let app_version: String?
    public let capabilities: [String]
    public let last_seen_at: String
}
