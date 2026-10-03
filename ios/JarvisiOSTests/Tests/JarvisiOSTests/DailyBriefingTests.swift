import XCTest
@testable import JarvisiOSCore

final class DailyBriefingTests: XCTestCase {
    private let now = CalendarDates.parse("2026-10-03T14:00:00Z")!
    private func health(seen: String = "2026-10-03T13:59:50Z", model: Bool = true) -> BriefingHealth {
        BriefingHealth(device: ["status": "online", "last_seen_at": seen,
            "health": ["model_ready": model, "mac_agent_connected": true, "worker_running": true]], now: now)
    }

    func testDecodesNativeEventKitCalendarContract() throws {
        let data = Data(##"{"id":"calendar-a","title":"Jarvis","color":"#000000","allows_modifications":true,"source_title":"iCloud","source_type":"2"}"##.utf8)
        let calendar = try JSONDecoder().decode(iOSCalendarInfoDTO.self, from: data)
        XCTAssertTrue(calendar.is_writable)
        XCTAssertTrue(calendar.is_icloud)
        XCTAssertFalse(calendar.is_on_my_mac)
    }

    func testMissingOrStaleHeartbeatNeverAnnouncesReady() {
        for health in [BriefingHealth(device: [:], now: now), self.health(seen: "2026-10-03T13:00:00Z")] {
            let text = DailyBriefing.text(now: now, events: nil, health: health, weather: "")
            XCTAssertFalse(text.contains("sistemi hazır"))
            XCTAssertTrue(text.contains("güncel bağlantı sinyali alınamadı"))
            XCTAssertTrue(text.contains("programını doğrulayamadım"))
            XCTAssertFalse(text.contains("etkinlik yok"))
        }
    }

    func testUnavailableModelIsNotReportedAsReady() {
        let text = DailyBriefing.text(now: now, events: [], health: health(model: false), weather: "18 derece")
        XCTAssertTrue(text.contains("Yerel model henüz hazır değil"))
        XCTAssertFalse(text.contains("sistemi hazır"))
    }

    func testFailedCalendarReadIsDifferentFromEmptyAgenda() {
        let failed = DailyBriefing.text(now: now, events: nil, health: health(), weather: "")
        let empty = DailyBriefing.text(now: now, events: [], health: health(), weather: "")
        XCTAssertTrue(failed.contains("takvim kontrolü tamamlanamadı"))
        XCTAssertFalse(failed.contains("takvim bağlantısı ve komut sistemi hazır"))
        XCTAssertTrue(empty.contains("etkinlik yok"))
        XCTAssertTrue(empty.contains("komut sistemi hazır"))
    }

    func testSummaryExcludesPastEventsAndNotesAndLimitsReadout() {
        let past = iOSCalendarEventDTO(id: "past", calendar_id: "cal", title: "Geçmiş etkinlik", start: "2026-10-03T10:00:00Z", end: "2026-10-03T11:00:00Z")
        let future = (15...18).map { hour in
            iOSCalendarEventDTO(id: "\(hour)", calendar_id: "cal", title: "Etkinlik \(hour)",
                start: "2026-10-03T\(hour):00:00Z", end: "2026-10-03T\(hour):30:00Z", notes: "Gizli notları okuma")
        }
        let text = DailyBriefing.text(now: now, events: future.reversed() + [past], health: health(), weather: "Sakarya, 18 derece, açık")
        XCTAssertTrue(text.contains("4 etkinliğin var"))
        XCTAssertTrue(text.contains("Saat 18:00: Etkinlik 15"))
        XCTAssertTrue(text.contains("Diğer etkinlikleri"))
        XCTAssertFalse(text.contains("Etkinlik 18"))
        XCTAssertFalse(text.contains("Geçmiş etkinlik"))
        XCTAssertFalse(text.contains("Gizli notları"))
        XCTAssertTrue(text.contains("18 derece, açık"))
    }

    func testDateUsesIstanbulAtUTCDayBoundary() {
        let text = DailyBriefing.text(now: CalendarDates.parse("2026-10-03T22:00:00Z")!, events: [], health: nil, weather: "")
        XCTAssertTrue(text.contains("4 Ekim 2026 Pazar"))
    }
}
