"""Quiet, persistent notification policy for actionable iOS deployment failures."""

import asyncio
import os
from pathlib import Path
import sqlite3
import time
from core.build_manager.state import NotificationEvent
from core.logging.setup import get_logger

logger = get_logger("jarvis.build_manager.notifier")


class BuildNotifier:
    """Report intervention needs at most once a day across process restarts.

    Build/install success and ordinary device unavailability stay in local logs.
    A notification is reserved before delivery so an uncertain send is not replayed.
    """

    STATE_PATH = Path.home() / ".cache/jarvis/build/notifications.sqlite"
    COOLDOWN_SECONDS = 24 * 60 * 60
    NOTIFICATION_MESSAGES: dict[NotificationEvent, tuple[str, str]] = {
        NotificationEvent.SIGNING_EXPIRED: (
            "Uygulama imzası yenilenmeli",
            "Jarvis’in iPhone’da çalışması için imza yenilemesi gerekiyor. iPhone’u aynı Wi-Fi ağına bağla ve kilidini aç."
        ),
        NotificationEvent.USER_ACTION_REQUIRED: (
            "Kurulum için yardım gerekiyor",
            "iPhone bağlantısını ve ekran kilidini kontrol et. Apple hesabı giriş istiyorsa Xcode’dan tamamla."
        ),
        NotificationEvent.BUILD_FAILED: (
            "Uygulama güncellemesi hazırlanamadı",
            "Jarvis güncellemesi derlenemedi. Ayrıntılar Mac’teki kurulum kayıtlarında."
        ),
        NotificationEvent.INSTALL_FAILED: (
            "Uygulama güncellemesi kurulamadı",
            "iPhone aynı Wi-Fi ağında ve kilidi açık olmalı. Ayrıntılar Mac’teki kurulum kayıtlarında."
        ),
    }

    @classmethod
    def _reserve_alert(cls, event: NotificationEvent) -> bool:
        path = Path(cls.STATE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as conn:
            os.chmod(path, 0o600)
            conn.execute("CREATE TABLE IF NOT EXISTS alerts (event TEXT PRIMARY KEY, sent_at REAL NOT NULL)")
            now = time.time()
            updated = conn.execute(
                """INSERT INTO alerts VALUES (?, ?) ON CONFLICT(event) DO UPDATE SET sent_at=excluded.sent_at
                   WHERE alerts.sent_at <= ?""", (event.value, now, now - cls.COOLDOWN_SECONDS),
            ).rowcount
            return updated == 1

    @classmethod
    async def notify(cls, event: NotificationEvent, detail: str | None = None) -> bool:
        if event not in cls.NOTIFICATION_MESSAGES:
            logger.debug("build_notification_suppressed_routine", notification_event=event.value)
            return False
        try:
            if not cls._reserve_alert(event):
                logger.debug("build_notification_suppressed_duplicate", notification_event=event.value)
                return False
            subtitle, body = cls.NOTIFICATION_MESSAGES[event]
            # Only fixed, generic text enters a banner. Diagnostic details may
            # contain account or path information and remain in the operation log.
            applescript = (f'display notification "{body}" with title "Jarvis" '
                           f'subtitle "{subtitle}"')
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", applescript,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            delivered = proc.returncode == 0
            logger.info("build_notification_result", notification_event=event.value, delivered=delivered)
            return delivered
        except Exception as exc:
            logger.warning("build_notification_failed", error_type=type(exc).__name__)
            return False
