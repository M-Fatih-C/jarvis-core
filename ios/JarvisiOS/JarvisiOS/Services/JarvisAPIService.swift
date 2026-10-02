import Foundation

public final class JarvisAPIService: @unchecked Sendable {
    public static let shared = JarvisAPIService()

    private var baseURLString: String {
        UserDefaults.standard.string(forKey: "jarvis_server_url") ?? "http://127.0.0.1:8765"
    }

    private var apiKey: String {
        UserDefaults.standard.string(forKey: "jarvis_api_key") ?? ""
    }

    private init() {}

    private var session: URLSession {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 15.0
        config.timeoutIntervalForResource = 30.0
        return URLSession(configuration: config)
    }

    // MARK: - Chat / Agent Commands
    public func sendChatMessage(prompt: String) async throws -> String {
        guard let url = URL(string: "\(baseURLString)/api/v1/chat") else {
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

        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let reply = json["response"] as? String {
            return reply
        }

        return "Yanıt alındı."
    }

    // MARK: - Task Proposals & Actions
    public func fetchProposals(status: String? = nil) async throws -> [TaskProposalDTO] {
        var urlString = "\(baseURLString)/api/v1/tasks/proposals"
        if let s = status {
            urlString += "?status=\(s)"
        }
        guard let url = URL(string: urlString) else { throw URLError(.badURL) }

        let (data, _) = try await session.data(from: url)
        return try JSONDecoder().decode([TaskProposalDTO].self, from: data)
    }

    public func fetchProposalDetail(taskId: String) async throws -> TaskProposalDetailDTO {
        guard let url = URL(string: "\(baseURLString)/api/v1/tasks/proposals/\(taskId)") else {
            throw URLError(.badURL)
        }
        let (data, _) = try await session.data(from: url)
        return try JSONDecoder().decode(TaskProposalDetailDTO.self, from: data)
    }

    public func requestApproval(actionId: String) async throws -> [String: Any] {
        guard let url = URL(string: "\(baseURLString)/api/v1/tasks/actions/\(actionId)/request-approval") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let (data, _) = try await session.data(for: req)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    public func approveAction(actionId: String) async throws -> TaskActionDTO {
        guard let url = URL(string: "\(baseURLString)/api/v1/tasks/actions/\(actionId)/approve") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let (data, _) = try await session.data(for: req)
        return try JSONDecoder().decode(TaskActionDTO.self, from: data)
    }

    public func dismissAction(actionId: String) async throws -> TaskActionDTO {
        guard let url = URL(string: "\(baseURLString)/api/v1/tasks/actions/\(actionId)/dismiss") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        let (data, _) = try await session.data(for: req)
        return try JSONDecoder().decode(TaskActionDTO.self, from: data)
    }

    public func planReminder(taskId: String, due: String? = nil) async throws -> TaskActionDTO {
        guard let url = URL(string: "\(baseURLString)/api/v1/tasks/proposals/\(taskId)/plan-reminder") else {
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
        guard let url = URL(string: "\(baseURLString)/api/v1/tasks/proposals/\(taskId)/plan-work-block") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: Any] = ["duration_minutes": duration]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, _) = try await session.data(for: req)
        struct Wrapper: Codable {
            let action: TaskActionDTO
        }
        let wrapped = try JSONDecoder().decode(Wrapper.self, from: data)
        return wrapped.action
    }

    // MARK: - Calendars & Health
    public func fetchCalendars() async throws -> [iOSCalendarInfoDTO] {
        guard let url = URL(string: "\(baseURLString)/api/v1/tasks/calendars") else {
            throw URLError(.badURL)
        }
        let (data, _) = try await session.data(from: url)
        struct Wrapper: Codable {
            let calendars: [iOSCalendarInfoDTO]
        }
        let res = try JSONDecoder().decode(Wrapper.self, from: data)
        return res.calendars
    }

    public func checkHealth() async throws -> [String: Any] {
        guard let url = URL(string: "\(baseURLString)/health") else {
            throw URLError(.badURL)
        }
        let (data, _) = try await session.data(from: url)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }
}
