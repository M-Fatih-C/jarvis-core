import Foundation

@MainActor
public final class JarvisAPIService {
    public static let shared = JarvisAPIService()

    private let queueService = FirebaseCommandQueueService.shared
    private init() {}

    private func decode<T: Decodable>(_ type: T.Type, _ value: Any) throws -> T {
        try JSONDecoder().decode(type, from: JSONSerialization.data(withJSONObject: value))
    }

    public func fetchEmails() async throws -> [iOSEmailItemDTO] {
        let result = try await queueService.screenRequest("client.emails")
        guard let items = result["items"] else { throw CommandQueueError.invalidResponse }
        return try decode([iOSEmailItemDTO].self, items)
    }

    public func fetchProposalDetails() async throws -> [TaskProposalDetailDTO] {
        let result = try await queueService.screenRequest("client.proposals")
        guard let items = result["details"] else { throw CommandQueueError.invalidResponse }
        return try decode([TaskProposalDetailDTO].self, items)
    }

    public func fetchProposalDetail(taskId: String) async throws -> TaskProposalDetailDTO {
        try decode(TaskProposalDetailDTO.self, await queueService.screenRequest("client.proposal", arguments: ["task_id": taskId]))
    }
    public func requestApproval(actionId: String) async throws -> [String: Any] {
        try await queueService.screenRequest("client.request_approval", arguments: ["action_id": actionId])
    }
    public func approveAction(_ action: TaskActionDTO, commandId: String) async throws -> TaskActionDTO {
        guard let approval = action.approval_id, let digest = action.action_digest else { throw CommandQueueError.invalidResponse }
        return try decode(TaskActionDTO.self, await queueService.approveTaskAction(actionId: action.id,
            approvalId: approval, digest: digest, commandId: commandId))
    }
    public func dismissAction(actionId: String) async throws -> TaskActionDTO {
        try decode(TaskActionDTO.self, await queueService.screenRequest("client.dismiss", arguments: ["action_id": actionId]))
    }
    public func planReminder(taskId: String, due: String? = nil) async throws -> TaskActionDTO {
        var arguments: [String: Any] = ["task_id": taskId]
        if let due { arguments["due"] = due }
        return try decode(TaskActionDTO.self, await queueService.screenRequest("client.plan_reminder", arguments: arguments))
    }
    public func planWorkBlock(taskId: String, duration: Int = 120) async throws -> TaskActionDTO {
        try decode(TaskActionDTO.self, await queueService.screenRequest("client.plan_work_block", arguments: ["task_id": taskId, "duration": duration]))
    }

    // MARK: - Calendar & System
    public func fetchCalendars() async throws -> [iOSCalendarInfoDTO] {
        let result = try await queueService.readCalendarTool("calendar.list_calendars")
        guard let calendars = result["calendars"] as? [[String: Any]] else { throw CommandQueueError.invalidResponse }
        return try JSONDecoder().decode([iOSCalendarInfoDTO].self, from: JSONSerialization.data(withJSONObject: calendars))
    }

    public func fetchEvents(calendarId: String?, start: Date, end: Date, timeout: Double = 45) async throws -> [iOSCalendarEventDTO] {
        let formatter = ISO8601DateFormatter()
        var arguments: [String: Any] = ["start": formatter.string(from: start), "end": formatter.string(from: end), "limit": 200]
        if let calendarId { arguments["calendar_ids"] = [calendarId] }
        let result = try await queueService.readCalendarTool("calendar.list_events", arguments: arguments, timeout: timeout)
        guard let events = result["events"] as? [[String: Any]] else { throw CommandQueueError.invalidResponse }
        return try JSONDecoder().decode([iOSCalendarEventDTO].self, from: JSONSerialization.data(withJSONObject: events))
    }

    public func checkHealth() async throws -> [String: Any] {
        guard let uid = FirebaseAuthService.shared.currentUserID,
              let project = FirebaseAuthService.shared.currentProjectID else {
            throw CommandQueueError.unauthenticated
        }
        return try await queueService.fetchDeviceStatus(userId: uid, projectId: project)
    }
}
