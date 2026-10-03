import Foundation

public enum TaskProposalStatusDTO: String, Codable, Sendable {
    case proposed = "PROPOSED"
    case dismissed = "DISMISSED"
    case waitingApproval = "WAITING_APPROVAL"
    case approved = "APPROVED"
    case executing = "EXECUTING"
    case executed = "EXECUTED"
    case failed = "FAILED"
}

public enum ActionTypeDTO: String, Codable, Sendable {
    case reminder
    case calendarEvent = "calendar_event"
    case workBlock = "work_block"
}

public struct TaskActionDTO: Identifiable, Codable, Sendable {
    public let action_id: String
    public let task_id: String
    public let source_message_id: String
    public let action_type: ActionTypeDTO
    public let target_destination: String
    public let title: String
    public let notes: String?
    public let start_time: String?
    public let end_time: String?
    public let due_date: String?
    public let target_calendar_id: String?
    public let approval_id: String?
    public let action_digest: String?
    public let status: TaskProposalStatusDTO
    public let external_id: String?
    public let error_message: String?

    public var id: String { action_id }
}

public struct TaskProposalDTO: Identifiable, Codable, Sendable {
    public let task_id: String
    public let source_message_id: String
    public let title: String
    public let description: String
    public let category: String
    public let priority: String
    public let deadline: String?
    public let deadline_confidence: String
    public let raw_deadline_text: String?
    public let proposed_action: String
    public let status: TaskProposalStatusDTO
    public let created_at: String

    public var id: String { task_id }
}

public struct TaskProposalDetailDTO: Codable, Sendable {
    public let task: TaskProposalDTO
    public let source_email_subject: String?
    public let source_email_sender: String?
    public let source_email_date: String?
    public let source_email_preview: String?
    public let actions: [TaskActionDTO]
}
