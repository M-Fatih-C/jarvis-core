import AppIntents
import Foundation

struct DailyBriefingIntent: AppIntent {
    static var title: LocalizedStringResource = "Yolculuk özetini al"
    static var description = IntentDescription("Tarih, kalan etkinlikler ve JARVIS bağlantısını kontrol eder. Hava durumu metnini Kestirmeler’den alır. Sonucu Metni Seslendir eylemine bağlayabilirsin.")
    static var openAppWhenRun = false
    static var authenticationPolicy: IntentAuthenticationPolicy = .alwaysAllowed

    @Parameter(title: "Hava durumu", description: "Kestirmeler’de güncel hava durumu koşullarını ve sıcaklığı içeren metin.", default: "")
    var weatherSummary: String

    static var parameterSummary: some ParameterSummary {
        Summary("Yolculuk özetini al; hava durumu: \(\.$weatherSummary)")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ReturnsValue<String> {
        .result(value: await DailyBriefingService.build(weather: weatherSummary))
    }
}

struct JarvisShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: DailyBriefingIntent(), phrases: ["\(.applicationName) yolculuk özeti"],
                    shortTitle: "Yolculuk özeti", systemImageName: "car.fill")
    }
}

@MainActor
enum DailyBriefingService {
    static func build(weather: String = "") async -> String {
        let now = Date()
        guard FirebaseAuthService.shared.isUnlocked else {
            return DailyBriefing.text(now: now, events: nil, health: nil, weather: weather)
                + " Güvenli oturum kilitli. Özel programını JARVIS’i Face ID ile açtıktan sonra uygulamada dinleyebilirsin."
        }
        let health = try? await JarvisAPIService.shared.checkHealth()
        let snapshot = health.map { BriefingHealth(device: $0, now: Date()) }
        var events: [iOSCalendarEventDTO]?
        if snapshot?.calendarReady == true && snapshot?.workerReady == true {
            let start = CalendarDates.calendar.startOfDay(for: now)
            let end = CalendarDates.calendar.date(byAdding: .day, value: 1, to: start)!
            events = try? await JarvisAPIService.shared.fetchEvents(calendarId: nil, start: start, end: end, timeout: 15)
        }
        return DailyBriefing.text(now: Date(), events: events,
            health: health.map { BriefingHealth(device: $0) }, weather: weather)
    }
}
