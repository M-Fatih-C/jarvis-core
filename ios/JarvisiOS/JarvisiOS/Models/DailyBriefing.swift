import Foundation

struct BriefingHealth {
    var connected: Bool
    var modelReady: Bool
    var calendarReady: Bool
    var workerReady: Bool

    init(device: [String: Any], now: Date = Date()) {
        let seen = CalendarDates.parse(device["last_seen_at"] as? String ?? "")
        let age = seen.map { now.timeIntervalSince($0) } ?? .infinity
        let status = device["status"] as? String
        connected = ["online", "degraded"].contains(status ?? "") && age >= -60 && age < 100
        let health = device["health"] as? [String: Any] ?? [:]
        modelReady = connected && health["model_ready"] as? Bool == true
        calendarReady = connected && health["mac_agent_connected"] as? Bool == true
        workerReady = connected && health["worker_running"] as? Bool == true
    }
}

enum DailyBriefing {
    /// A deterministic spoken summary, without model inference or calendar writes.
    static func text(now: Date, events: [iOSCalendarEventDTO]?, health: BriefingHealth?, weather: String) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "tr_TR")
        formatter.timeZone = CalendarDates.calendar.timeZone
        formatter.dateFormat = "d MMMM yyyy EEEE"
        var lines = ["Merhaba patron. Bugün \(formatter.string(from: now))."]
        let weather = String(weather.trimmingCharacters(in: .whitespacesAndNewlines).prefix(300))
        lines.append(weather.isEmpty ? "Hava durumu bilgisi alınmadı." : "Hava durumu: \(weather).")
        if let events {
            let endOfDay = CalendarDates.calendar.date(byAdding: .day, value: 1,
                to: CalendarDates.calendar.startOfDay(for: now))!
            let remaining = events.filter {
                guard let start = CalendarDates.parse($0.start), let end = CalendarDates.parse($0.end) else { return false }
                return end > now && start < endOfDay
            }.sorted { (CalendarDates.parse($0.start) ?? .distantPast) < (CalendarDates.parse($1.start) ?? .distantPast) }
            if remaining.isEmpty {
                lines.append("Bugünün kalanında takviminde etkinlik yok.")
            } else {
                lines.append("Günün kalanında \(remaining.count) etkinliğin var.")
                for event in remaining.prefix(3) {
                    let title = String(event.title.replacingOccurrences(of: "\n", with: " ").prefix(100))
                    let start = CalendarDates.parse(event.start)!
                    let time = event.all_day ? "Tüm gün" : start <= now ? "Şu anda" : "Saat \(CalendarDates.time(start))"
                    lines.append("\(time): \(title).")
                }
                if remaining.count > 3 { lines.append("Diğer etkinlikleri takvimde görebilirsin.") }
            }
        } else {
            lines.append("Takvimine şu anda ulaşamıyorum; programını doğrulayamadım.")
        }
        if let health, health.connected {
            if health.modelReady && health.calendarReady && health.workerReady {
                lines.append(events == nil ? "Yerel model ve Mac bağlantısı hazır, takvim kontrolü tamamlanamadı."
                             : "Yerel model, takvim bağlantısı ve komut sistemi hazır.")
            } else {
                if !health.modelReady { lines.append("Yerel model henüz hazır değil.") }
                if !health.calendarReady { lines.append("Mac takvim bağlantısı hazır değil.") }
                if !health.workerReady { lines.append("Komut sistemi henüz hazır değil.") }
            }
        } else {
            lines.append("Mac’ten güncel bağlantı sinyali alınamadı.")
        }
        lines.append("İyi yolculuklar.")
        return lines.joined(separator: " ")
    }
}
