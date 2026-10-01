"""Unit tests for EmailNotificationService, content sanitization, deduplication, and EmailSyncScheduler."""

from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from core.email_analysis.schemas import (
    EmailAnalysisResult,
    EmailCategory,
    ImportanceLevel,
    TaskProposal,
)
from core.notifications.email_notifier import (
    EmailNotificationService,
    sanitize_notification_body,
)
from integrations.gmail.models import NormalizedEmail
from integrations.gmail.pipeline import GmailProcessingPipeline
from integrations.gmail.scheduler import EmailSyncScheduler
from integrations.gmail.storage import EmailStorage
from integrations.macos.client import MacBridgeClient


@pytest.fixture
def memory_storage() -> EmailStorage:
    storage = EmailStorage(db_path=":memory:")
    yield storage
    storage.close()


def test_sanitize_notification_body() -> None:
    raw_body = "Doğrulama kodunuz 849201. Şifreniz: GizliParola123! Lütfen https://verylonglinkdomain.com/auth/verify?session=abc123xyz456adresini ziyaret edin."
    sanitized = sanitize_notification_body(raw_body)
    assert "[KOD]" in sanitized
    assert "849201" not in sanitized
    assert "[GİZLENDİ]" in sanitized
    assert "GizliParola123!" not in sanitized


def test_notification_importance_filtering(memory_storage: EmailStorage) -> None:
    service = EmailNotificationService(storage=memory_storage)

    # 1. Promotional email -> NO notification
    promo = EmailAnalysisResult(
        category=EmailCategory.PROMOTIONAL,
        importance=ImportanceLevel.LOW,
        summary="Yaz indirimi başladı.",
        proposed_action="İncele",
    )
    assert service.should_notify(promo) is False

    # 2. Urgent security alert -> Notify
    security = EmailAnalysisResult(
        category=EmailCategory.FINANCE,
        importance=ImportanceLevel.HIGH,
        security_or_payment=True,
        summary="Hesabınıza yeni bir cihazdan giriş yapıldı.",
        proposed_action="Girişi onayla veya şifreni değiştir",
    )
    assert service.should_notify(security) is True

    # 3. Education deadline -> Notify
    deadline = EmailAnalysisResult(
        category=EmailCategory.EDUCATION,
        importance=ImportanceLevel.HIGH,
        has_deadline=True,
        summary="Kayıt yenileme son tarihi yaklaşıyor.",
        proposed_action="Kayıt ol",
    )
    assert service.should_notify(deadline) is True


@pytest.mark.asyncio
async def test_notification_dispatch_and_deduplication(memory_storage: EmailStorage) -> None:
    mock_bridge = AsyncMock(spec=MacBridgeClient)
    service = EmailNotificationService(storage=memory_storage, bridge_client=mock_bridge)

    email = NormalizedEmail(
        message_id="msg_notify_1",
        thread_id="t1",
        sender="HR Team <hr@tech.com>",
        recipient="user@test.com",
        subject="Teknik Mülakat Daveti",
        received_at=datetime.now(timezone.utc),
        body_plain="Yarın saat 14:00'da görüşmek üzere.",
        content_hash="mock_hash_hr",
    )

    analysis = EmailAnalysisResult(
        category=EmailCategory.WORK_CAREER,
        importance=ImportanceLevel.HIGH,
        requires_response=True,
        summary="Teknik mülakat daveti iletildi.",
        proposed_action="Daveti yanıtla",
    )

    # 1. First dispatch -> Sends notification and records
    dispatched1 = await service.notify_if_important(email, analysis)
    assert dispatched1 is True
    assert mock_bridge.call.call_count == 1

    # 2. Second dispatch -> Deduplicated, bridge not called again
    dispatched2 = await service.notify_if_important(email, analysis)
    assert dispatched2 is False
    assert mock_bridge.call.call_count == 1


def test_scheduler_explicit_consent_guard() -> None:
    mock_pipeline = MagicMock(spec=GmailProcessingPipeline)
    # Default: disabled without explicit consent
    scheduler = EmailSyncScheduler(pipeline=mock_pipeline, enabled=False)
    scheduler.start()
    assert scheduler._running is False

    # Explicit consent enabled
    scheduler.set_user_consent(True)
    assert scheduler.enabled is True


def test_scheduler_missed_sync_recovery() -> None:
    mock_pipeline = MagicMock(spec=GmailProcessingPipeline)
    scheduler = EmailSyncScheduler(
        pipeline=mock_pipeline,
        sync_times=["09:00", "20:00"],
        enabled=True,
    )

    # Simulated last run yesterday at 21:00
    tz = scheduler._get_tz()
    yesterday_21 = datetime(2026, 9, 30, 21, 0, tzinfo=tz)

    # Current time is today at 11:00 (09:00 sync was missed while Mac slept)
    today_11 = datetime(2026, 10, 1, 11, 0, tzinfo=tz)

    assert scheduler.has_missed_sync(yesterday_21, today_11) is True

    # But between 11:00 and 12:00 today, no scheduled sync occurred
    today_12 = datetime(2026, 10, 1, 12, 0, tzinfo=tz)
    assert scheduler.has_missed_sync(today_11, today_12) is False
