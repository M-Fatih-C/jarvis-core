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


@pytest.mark.asyncio
async def test_new_mail_boundary_survives_restart_and_preserves_backlog(tmp_path, sample_email):
    path = str(tmp_path / "mail.db")
    store = EmailStorage(path, encryption_enabled=False)
    cutoff = sample_email.received_at + timedelta(hours=1)
    await store.save_email(sample_email, "user@test.com")
    fresh = sample_email.model_copy(update={"message_id": "new", "content_hash": "new-hash", "received_at": cutoff + timedelta(seconds=1)})
    await store.save_email(fresh, "user@test.com")
    await store.set_monitoring_start(cutoff)
    store.close()
    store = EmailStorage(path, encryption_enabled=False)
    assert await store.get_monitoring_start() == cutoff
    assert await store.pending_analysis_ids() == ["new"]
    assert await store.get_email(sample_email.message_id) is not None
    store.close()


@pytest.mark.asyncio
async def test_sync_rejects_old_mail_returned_by_history(memory_storage, sample_email):
    cutoff = sample_email.received_at + timedelta(hours=1)
    await memory_storage.set_monitoring_start(cutoff)
    client = AsyncMock(spec=GmailClient)
    normalizer = MagicMock(spec=GmailNormalizer)
    normalizer.normalize_message.return_value = sample_email
    sync = GmailSyncService(client, memory_storage, normalizer=normalizer)
    from integrations.gmail.models import SyncResult
    result = SyncResult()
    messages = await sync._fetch_and_persist_messages([{"id": sample_email.message_id}], "user@test.com", "default", result)
    assert messages == []
    assert result.skipped_count == 1
    assert await memory_storage.get_email(sample_email.message_id) is None


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

    # Second sync (incremental via users.history.list)
    mock_client.get_history.return_value = {
        "history": [
            {
                "messagesAdded": [{"message": {"id": "msg_test_101", "threadId": "t1"}}],
            }
        ],
        "historyId": "hist_new_1000",
    }
    res2, emails2 = await sync_service.sync("user@test.com")
    # Message was already stored during initial sync -> skipped, no duplicates
    assert res2.new_count == 0
    assert res2.skipped_count == 1
    assert len(emails2) == 0

    # Verify historyId advanced to latest
    state_after = await memory_storage.get_sync_state("user@test.com")
    assert state_after is not None
    assert state_after.last_history_id == "hist_new_1000"


@pytest.mark.asyncio
async def test_sync_service_incremental_history_add_and_delete(
    memory_storage: EmailStorage, sample_email: NormalizedEmail
) -> None:
    # First save an existing email
    await memory_storage.save_email(sample_email, "user@test.com")

    # Set existing sync state with history_id
    await memory_storage.save_sync_state(
        EmailSyncState(
            account_email="user@test.com",
            last_synced_at=datetime.now(timezone.utc),
            last_history_id="hist_100",
            total_messages_synced=1,
        )
    )

    mock_client = AsyncMock(spec=GmailClient)
    mock_client.get_profile.return_value = GmailAccountProfile(
        email_address="user@test.com", history_id="hist_200"
    )
    mock_client.get_history.return_value = {
        "history": [
            {
                "messagesAdded": [{"message": {"id": "msg_new_202", "threadId": "t2"}}],
                "messagesDeleted": [{"message": {"id": "msg_test_101", "threadId": "t1"}}],
            }
        ],
        "historyId": "hist_200",
    }
    mock_client.get_message.return_value = {
        "id": "msg_new_202",
        "threadId": "t2",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Yeni E-posta"},
                {"name": "From", "value": "sender@test.com"},
                {"name": "Date", "value": "Thu, 01 Oct 2026 14:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": "WWVuaSBpY2VyaWs="},
        },
    }

    sync_service = GmailSyncService(client=mock_client, storage=memory_storage)
    res, new_emails = await sync_service.sync("user@test.com")

    assert res.new_count == 1
    assert len(new_emails) == 1
    assert new_emails[0].message_id == "msg_new_202"

    # Verify deleted message was removed from storage
    assert not await memory_storage.is_email_processed("msg_test_101")
    # Verify new message is stored
    assert await memory_storage.is_email_processed("msg_new_202")

    # Verify sync cursor advanced
    state = await memory_storage.get_sync_state("user@test.com")
    assert state.last_history_id == "hist_200"


@pytest.mark.asyncio
async def test_sync_history_expired_fallback(memory_storage: EmailStorage) -> None:
    from integrations.gmail.exceptions import GmailHistoryExpiredError

    await memory_storage.save_sync_state(
        EmailSyncState(
            account_email="user@test.com",
            last_synced_at=datetime.now(timezone.utc),
            last_history_id="hist_too_old_99",
        )
    )

    mock_client = AsyncMock(spec=GmailClient)
    mock_client.get_profile.return_value = GmailAccountProfile(
        email_address="user@test.com", history_id="hist_fresh_500"
    )
    # get_history raises GmailHistoryExpiredError (HTTP 404)
    mock_client.get_history.side_effect = GmailHistoryExpiredError("Cursor expired")
    mock_client.list_messages.return_value = ([], None)

    sync_service = GmailSyncService(client=mock_client, storage=memory_storage)
    res, emails = await sync_service.sync("user@test.com")

    # Fallback to bounded query succeeded without crash
    assert res.failed_count == 0
    state = await memory_storage.get_sync_state("user@test.com")
    assert state.last_history_id == "hist_fresh_500"


@pytest.mark.asyncio
async def test_storage_body_encryption_at_rest(sample_email: NormalizedEmail) -> None:
    # Use memory storage with active encryption
    storage = EmailStorage(db_path=":memory:", encryption_enabled=True)
    await storage.save_email(sample_email, "user@test.com")

    # Query raw database row directly using raw connection
    cursor = storage._conn.execute("SELECT body_plain, body_preview FROM emails WHERE message_id = ?", ("msg_test_101",))
    row = cursor.fetchone()
    raw_body = row["body_plain"]

    # Raw body in database must NOT be plaintext! Must be encrypted ciphertext JSON payload!
    assert "Lütfen projenizi 8 Ekim" not in raw_body
    assert '"ciphertext":' in raw_body
    assert '"algorithm": "AES-256-GCM"' in raw_body

    # But calling get_email transparently decrypts the body for authorized use
    retrieved = await storage.get_email("msg_test_101")
    assert retrieved is not None
    assert retrieved.body_plain == "Lütfen projenizi 8 Ekim tarihine kadar yükleyiniz."
    storage.close()


@pytest.mark.asyncio
async def test_storage_pruning_and_account_data_purge(sample_email: NormalizedEmail) -> None:
    storage = EmailStorage(db_path=":memory:")
    await storage.save_email(sample_email, "user@test.com")
    await storage.save_sync_state(EmailSyncState(account_email="user@test.com"))

    # 1. Prune older bodies
    pruned = await storage.prune_retained_bodies(older_than_days=0)
    assert pruned == 1
    cleared = await storage.get_email("msg_test_101")
    assert cleared.body_plain == ""
    assert cleared.subject == "Dönem Projesi Teslim Tarihi"  # Metadata retained!

    # 2. Account disconnect purge
    await storage.clear_account_data("user@test.com")
    assert await storage.get_email("msg_test_101") is None
    assert await storage.get_sync_state("user@test.com") is None
    storage.close()


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


@pytest.mark.asyncio
async def test_client_retry_after_rate_limiting_429() -> None:
    import httpx
    from integrations.gmail.auth import GmailOAuthManager

    attempts = 0

    def mock_transport_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "0.01"})
        return httpx.Response(200, json={"emailAddress": "test@jarvis.local", "historyId": "123"})

    mock_auth = AsyncMock(spec=GmailOAuthManager)
    mock_auth.get_valid_access_token.return_value = "ya29.test_token"

    transport = httpx.MockTransport(mock_transport_handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GmailClient(
            auth_manager=mock_auth,
            http_client=http_client,
            max_retries=2,
            base_backoff_seconds=0.01,
        )
        profile = await client.get_profile()
        assert attempts == 2
        assert profile.email_address == "test@jarvis.local"


@pytest.mark.asyncio
async def test_sync_history_pagination(memory_storage: EmailStorage) -> None:
    # Set existing sync state with history_id
    await memory_storage.save_sync_state(
        EmailSyncState(
            account_email="user@test.com",
            last_synced_at=datetime.now(timezone.utc),
            last_history_id="hist_page_0",
        )
    )

    mock_client = AsyncMock(spec=GmailClient)
    mock_client.get_profile.return_value = GmailAccountProfile(
        email_address="user@test.com", history_id="hist_page_2"
    )

    async def mock_get_history(start_history_id: str, max_results: int = 100, page_token: str | None = None, account: str = "default"):
        if page_token is None:
            return {
                "history": [
                    {"messagesAdded": [{"message": {"id": "msg_page1", "threadId": "t1"}}]}
                ],
                "nextPageToken": "page_token_2",
                "historyId": "hist_page_1",
            }
        else:
            return {
                "history": [
                    {"messagesAdded": [{"message": {"id": "msg_page2", "threadId": "t2"}}]}
                ],
                "historyId": "hist_page_2",
            }

    mock_client.get_history.side_effect = mock_get_history
    mock_client.get_message.side_effect = lambda msg_id, **kw: {
        "id": msg_id,
        "threadId": "t",
        "payload": {
            "headers": [
                {"name": "Subject", "value": f"Subject for {msg_id}"},
                {"name": "From", "value": "sender@test.com"},
                {"name": "Date", "value": "Thu, 01 Oct 2026 14:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": "WWVuaSBpY2VyaWs="},
        },
    }

    sync_service = GmailSyncService(client=mock_client, storage=memory_storage, max_messages_per_sync=10)
    res, new_emails = await sync_service.sync("user@test.com")

    assert res.new_count == 2
    assert len(new_emails) == 2
    assert {e.message_id for e in new_emails} == {"msg_page1", "msg_page2"}

    state = await memory_storage.get_sync_state("user@test.com")
    assert state.last_history_id == "hist_page_2"


@pytest.mark.asyncio
async def test_sync_interruption_does_not_advance_cursor(memory_storage: EmailStorage) -> None:
    initial_time = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)
    await memory_storage.save_sync_state(
        EmailSyncState(
            account_email="user@test.com",
            last_synced_at=initial_time,
            last_history_id="hist_safe_anchor",
            status="idle",
        )
    )

    mock_client = AsyncMock(spec=GmailClient)
    mock_client.get_profile.return_value = GmailAccountProfile(
        email_address="user@test.com", history_id="hist_unsafe_advanced"
    )
    # Raising an unhandled exception during history fetch (e.g. unexpected network crash)
    mock_client.get_history.side_effect = RuntimeError("Simulated process crash / network abort")

    sync_service = GmailSyncService(client=mock_client, storage=memory_storage)
    res, emails = await sync_service.sync("user@test.com")

    assert res.failed_count == 1
    assert "Simulated process crash" in res.errors[0]

    # Verify sync cursor DID NOT advance
    state = await memory_storage.get_sync_state("user@test.com")
    assert state.last_history_id == "hist_safe_anchor"
    assert state.status == "error"
    assert "Simulated process crash" in (state.error_message or "")


@pytest.mark.asyncio
async def test_sync_duplicate_message_ids_in_history(memory_storage: EmailStorage) -> None:
    await memory_storage.save_sync_state(
        EmailSyncState(
            account_email="user@test.com",
            last_synced_at=datetime.now(timezone.utc),
            last_history_id="hist_dup_0",
        )
    )

    mock_client = AsyncMock(spec=GmailClient)
    mock_client.get_profile.return_value = GmailAccountProfile(
        email_address="user@test.com", history_id="hist_dup_1"
    )
    # History contains the same message ID duplicated across records
    mock_client.get_history.return_value = {
        "history": [
            {"messagesAdded": [{"message": {"id": "msg_dup_1", "threadId": "t1"}}]},
            {"messagesAdded": [{"message": {"id": "msg_dup_1", "threadId": "t1"}}]},
        ],
        "historyId": "hist_dup_1",
    }
    mock_client.get_message.return_value = {
        "id": "msg_dup_1",
        "threadId": "t1",
        "payload": {
            "headers": [
                {"name": "Subject", "value": "Single Subject"},
                {"name": "From", "value": "sender@test.com"},
                {"name": "Date", "value": "Thu, 01 Oct 2026 14:00:00 +0000"},
            ],
            "mimeType": "text/plain",
            "body": {"data": "VGVzdA=="},
        },
    }

    sync_service = GmailSyncService(client=mock_client, storage=memory_storage)
    res, new_emails = await sync_service.sync("user@test.com")

    # Stored once, second skipped, exactly 1 new email
    assert res.new_count == 1
    assert res.skipped_count == 1
    assert len(new_emails) == 1

