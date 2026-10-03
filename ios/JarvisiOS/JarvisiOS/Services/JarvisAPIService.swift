import Foundation

@MainActor
public final class JarvisAPIService {
    public static let shared = JarvisAPIService()

    private let session = URLSession.shared
    private let queueService = FirebaseCommandQueueService.shared

    private var baseURLString: String {
        return UserDefaults.standard.string(forKey: "jarvis_server_url") ?? "http://localhost:8765"
    }

    private init() {}

    // MARK: - Direct Chat API (Local Mac Fallback / Dev Proxy)
    public func sendChatMessage(prompt: String) async throws -> String {
        guard let url = URL(string: "\(baseURLString)/v1/chat") else {
            throw URLError(.badURL)
        }

        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["message": prompt]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await session.data(for: req)
        guard let httpRes = response as? HTTPURLResponse, (200...299).contains(httpRes.statusCode) else {
            throw NSError(domain: "JarvisAPI", code: 1, userInfo: [NSLocalizedDescriptionKey: "Failed to send message to Jarvis."])
        }

        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            // Python API exposes {run_id: ..., status: ..., message: ..., approval: ...}
            if let reply = json["message"] as? String {
                return reply
            } else if let reply = json["response"] as? String {
                return reply
            }
        }

        return "Yanıt işlendi."
    }

    // MARK: - Email Summaries
    public func fetchEmails() async throws -> [iOSEmailItemDTO] {
        guard let url = URL(string: "\(baseURLString)/v1/emails/summaries") else {
            throw URLError(.badURL)
        }
        do {
            let (data, response) = try await session.data(from: url)
            guard let httpRes = response as? HTTPURLResponse, (200...299).contains(httpRes.statusCode) else {
                return []
            }
            return (try? JSONDecoder().decode([iOSEmailItemDTO].self, from: data)) ?? []
        } catch {
            return []
        }
    }

    // MARK: - Task Proposals & Actions
    public func fetchProposals(status: String? = nil) async throws -> [TaskProposalDTO] {
        var urlString = "\(baseURLString)/v1/tasks/proposals"
        if let s = status {
            urlString += "?status=\(s)"
        }
        guard let url = URL(string: urlString) else { throw URLError(.badURL) }

        do {
            let (data, response) = try await session.data(from: url)
            guard let httpRes = response as? HTTPURLResponse, (200...299).contains(httpRes.statusCode) else {
                return []
            }
            return try JSONDecoder().decode([TaskProposalDTO].self, from: data)
        } catch {
            return []
        }
    }

    public func fetchProposalDetail(taskId: String) async throws -> TaskProposalDetailDTO {
        guard let url = URL(string: "\(baseURLString)/v1/tasks/proposals/\(taskId)") else {
            throw URLError(.badURL)
        }
        let (data, _) = try await session.data(from: url)
        return try JSONDecoder().decode(TaskProposalDetailDTO.self, from: data)
    }

    public func requestApproval(actionId: String) async throws -> [String: Any] {
        guard let url = URL(string: "\(baseURLString)/v1/tasks/actions/\(actionId)/request-approval") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let (data, _) = try await session.data(for: req)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    public func approveAction(actionId: String) async throws -> TaskActionDTO {
        guard let url = URL(string: "\(baseURLString)/v1/tasks/actions/\(actionId)/approve") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let (data, _) = try await session.data(for: req)
        return try JSONDecoder().decode(TaskActionDTO.self, from: data)
    }

    public func dismissAction(actionId: String) async throws -> TaskActionDTO {
        guard let url = URL(string: "\(baseURLString)/v1/tasks/actions/\(actionId)/dismiss") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let (data, _) = try await session.data(for: req)
        return try JSONDecoder().decode(TaskActionDTO.self, from: data)
    }

    public func planReminder(taskId: String, due: String? = nil) async throws -> TaskActionDTO {
        guard let url = URL(string: "\(baseURLString)/v1/tasks/proposals/\(taskId)/plan-reminder") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = due != nil ? ["custom_due_date": due!] : [:]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, _) = try await session.data(for: req)
        return try JSONDecoder().decode(TaskActionDTO.self, from: data)
    }

    public func planWorkBlock(taskId: String, duration: Int = 120) async throws -> TaskActionDTO {
        guard let url = URL(string: "\(baseURLString)/v1/tasks/proposals/\(taskId)/plan-work-block") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["duration_minutes": duration]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, _) = try await session.data(for: req)
        return try JSONDecoder().decode(TaskActionDTO.self, from: data)
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
        guard let uid = KeychainHelper.shared.read(key: "firebase_user_uid"),
              let project = KeychainHelper.shared.read(key: "firebase_project_id") else {
            throw CommandQueueError.unauthenticated
        }
        return try await queueService.fetchDeviceStatus(userId: uid, projectId: project)
    }
}
