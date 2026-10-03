"""Read-phase transaction aborts must not break a competing worker's poll."""
from unittest.mock import AsyncMock, MagicMock

from google.api_core.exceptions import Aborted, DeadlineExceeded
import pytest

from core.config.settings import Settings
from integrations.firebase.command_repository import FirestoreCommandRepository


def repository():
    provider = MagicMock()
    provider.get_client.return_value.transaction.side_effect = lambda: object()
    return FirestoreCommandRepository(client_provider=provider, settings=Settings(firebase_enabled=False))


@pytest.mark.asyncio
async def test_aborted_read_rechecks_with_a_new_transaction(monkeypatch):
    monkeypatch.setattr("integrations.firebase.command_repository.asyncio.sleep", AsyncMock())
    repo = repository()
    claim = AsyncMock(side_effect=[Aborted("contention"), None])
    # The winning worker has already leased it by the second read.
    assert await repo._claim_with_retry(claim) is None
    attempts = claim.await_args_list
    assert len(attempts) == 2
    assert attempts[0].args[0] is not attempts[1].args[0]


@pytest.mark.asyncio
async def test_sdk_rollback_error_retains_the_abort_for_retry(monkeypatch):
    monkeypatch.setattr("integrations.firebase.command_repository.asyncio.sleep", AsyncMock())
    rollback = ValueError("The transaction has no transaction ID")
    rollback.__context__ = Aborted("Transaction lock timeout")
    claim = AsyncMock(side_effect=[rollback, "claimed"])
    assert await repository()._claim_with_retry(claim) == "claimed"
    assert claim.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [DeadlineExceeded("uncertain"), ValueError("bad schema")])
async def test_uncertain_or_permanent_failures_are_not_retried(error):
    claim = AsyncMock(side_effect=error)
    with pytest.raises(type(error)):
        await repository()._claim_with_retry(claim)
    assert claim.await_count == 1


@pytest.mark.asyncio
async def test_contention_retry_is_bounded(monkeypatch):
    monkeypatch.setattr("integrations.firebase.command_repository.asyncio.sleep", AsyncMock())
    claim = AsyncMock(side_effect=Aborted("busy"))
    with pytest.raises(Aborted):
        await repository()._claim_with_retry(claim)
    assert claim.await_count == 3
