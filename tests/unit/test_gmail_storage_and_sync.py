"""Unit tests for EmailStorage SQLite persistence and GmailSyncService incremental synchronization."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from integrations.gmail.client import GmailClient
from integrations.gmail.exceptions import GmailMessageNotFoundError
from integrations.gmail.models import (
    EmailSyncState,
    GmailAccountProfile,
    NormalizedEmail,
)
from integrations.gmail.normalizer import GmailNormalizer
from integrations.gmail.storage import EmailStorage
from integrations.gmail.sync import GmailSyncService


@pytest.fixture
def memory_storage() -> EmailStorage:
    storage = EmailStorage(db_path=":memory:")
    yield storage
    storage.close()


@pytest.fixture
def sample_email() -> NormalizedEmail:
    return NormalizedEmail(
        message_id="msg_test_101",
        thread_id="thread_test_101",
        sender="Academic Office <academic@univ.edu>",
        sender_email="academic@univ.edu",
        recipient="user@test.com",
        subject="Dönem Projesi Teslim Tarihi",
        received_at=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        body_plain="Lütfen projenizi 8 Ekim tarihine kadar yükleyiniz.",
        content_hash="mock_hash_101",
    )


@pytest.mark.asyncio
async def test_email_storage_crud_and_deduplication(
    memory_storage: EmailStorage, sample_email: NormalizedEmail
) -> None:
    # 1. Initially email is not processed
    assert not await memory_storage.is_email_processed("msg_test_101")

    # 2. Save email succeeds
    saved = await memory_storage.save_email(sample_email, "user@test.com")
    assert saved is True
    assert await memory_storage.is_email_processed("msg_test_101")

    # 3. Duplicate save returns False
    duplicate = await memory_storage.save_email(sample_email, "user@test.com")
    assert duplicate is False

    # 4. Fetch email
    retrieved = await memory_storage.get_email("msg_test_101")
    assert retrieved is not None
    assert retrieved.message_id == "msg_test_101"
    assert retrieved.subject == "Dönem Projesi Teslim Tarihi"


@pytest.mark.asyncio
async def test_email_sync_state_persistence(memory_storage: EmailStorage) -> None:
    now = datetime.now(timezone.utc)
    state = EmailSyncState(
        account_email="user@test.com",
        last_synced_at=now,
        last_history_id="hist_12345",
        total_messages_synced=15,
        status="idle",
    )
    await memory_storage.save_sync_state(state)

    loaded = await memory_storage.get_sync_state("user@test.com")
    assert loaded is not None
    assert loaded.account_email == "user@test.com"
    assert loaded.last_history_id == "hist_12345"
    assert loaded.total_messages_synced == 15
    assert loaded.status == "idle"


@pytest.mark.asyncio
async def test_sync_service_initial_and_incremental_sync(
    memory_storage: EmailStorage, sample_email: NormalizedEmail
) -> None:
    mock_client = AsyncMock(spec=GmailClient)
    mock_client.get_profile.return_value = GmailAccountProfile(
        email_address="user@test.com",
        messages_total=100,
        threads_total=50,
        history_id="hist_init_999",
    )
    mock_client.list_messages.return_value = ([{"id": "msg_test_101", "threadId": "t1"}], None)
    mock_client.get_message.return_value = {
        "id": "msg_test_101",
        "threadId": "t1",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Dönem Projesi Teslim Tarihi"},
                {"name": "From", "value": "academic@univ.edu"},
                {"name": "Date", "value": "Thu, 01 Oct 2026 12:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": "TGx0ZmVuIHByb2plbml6aSA4IEVraW0gdGFyaWhpbmUga2FkYXIgeVx1MDBlY2tsZXlpbml6Lg=="},
        },
    }

    sync_service = GmailSyncService(
        client=mock_client,
        storage=memory_storage,
        lookback_days=7,
        max_messages_per_sync=25,
    )

    # Initial sync
    res1, emails1 = await sync_service.sync("user@test.com")
    assert res1.new_count == 1
    assert len(emails1) == 1
    assert emails1[0].message_id == "msg_test_101"

    # Verify query used lookback
    call_args = mock_client.list_messages.call_args[1]
    assert "after:" in call_args["query"]

    # Verify sync state updated
    persisted_state = await memory_storage.get_sync_state("user@test.com")
    assert persisted_state is not None
    assert persisted_state.last_history_id == "hist_init_999"
    assert persisted_state.total_messages_synced == 1

    # Second sync (incremental): message is already processed -> skipped
    res2, emails2 = await sync_service.sync("user@test.com")
    assert res2.new_count == 0
    assert res2.skipped_count == 1
    assert len(emails2) == 0


@pytest.mark.asyncio
async def test_sync_deleted_message_tolerance(memory_storage: EmailStorage) -> None:
    mock_client = AsyncMock(spec=GmailClient)
    mock_client.get_profile.return_value = GmailAccountProfile(
        email_address="user@test.com", history_id="hist_1"
    )
    mock_client.list_messages.return_value = ([{"id": "msg_deleted_404", "threadId": "t2"}], None)
    mock_client.get_message.side_effect = GmailMessageNotFoundError("Resource not found")

    sync_service = GmailSyncService(client=mock_client, storage=memory_storage)
    res, emails = await sync_service.sync("user@test.com")

    assert res.skipped_count == 1
    assert res.new_count == 0
    assert res.failed_count == 0
    assert len(emails) == 0
