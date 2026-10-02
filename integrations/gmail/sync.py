"""Gmail synchronization engine supporting initial lookback, incremental sync, and state persistence."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time
from typing import Any

from core.logging.setup import get_logger
from integrations.gmail.client import GmailClient
from integrations.gmail.exceptions import (
    GmailAuthError,
    GmailHistoryExpiredError,
    GmailIntegrationError,
    GmailMessageNotFoundError,
    GmailNetworkError,
)
from integrations.gmail.models import EmailSyncState, NormalizedEmail, SyncResult
from integrations.gmail.normalizer import GmailNormalizer
from integrations.gmail.storage import EmailStorage

logger = get_logger("jarvis.gmail.sync")


class GmailSyncService:
    """Manages initial and incremental synchronization of Gmail messages with persistent state."""

    def __init__(
        self,
        client: GmailClient,
        storage: EmailStorage,
        normalizer: GmailNormalizer | None = None,
        lookback_days: int = 7,
        max_messages_per_sync: int = 25,
    ) -> None:
        self.client = client
        self.storage = storage
        self.normalizer = normalizer or GmailNormalizer()
        self.lookback_days = lookback_days
        self.max_messages = max_messages_per_sync
        self._sync_lock = asyncio.Lock()

    async def _fetch_and_persist_messages(
        self,
        message_stubs: list[dict[str, Any]],
        account_email: str,
        account: str,
        result: SyncResult,
    ) -> list[NormalizedEmail]:
        """Fetch, normalize, and store message stubs."""
        new_emails: list[NormalizedEmail] = []
        for stub in message_stubs:
            msg_id = stub.get("id", "")
            if not msg_id:
                continue

            if await self.storage.is_email_processed(msg_id):
                result.skipped_count += 1
                continue

            try:
                raw_msg = await self.client.get_message(msg_id, format_type="full", account=account)
                normalized = self.normalizer.normalize_message(raw_msg)
                saved = await self.storage.save_email(normalized, account_email)
                if saved:
                    result.new_count += 1
                    new_emails.append(normalized)
                else:
                    result.skipped_count += 1
            except GmailMessageNotFoundError:
                logger.warning("gmail_message_deleted_or_missing", msg_id=msg_id)
                result.skipped_count += 1
            except Exception as msg_exc:
                logger.error("gmail_message_processing_failed", msg_id=msg_id, error=str(msg_exc))
                result.failed_count += 1
                result.errors.append(f"Message {msg_id}: {msg_exc}")

        return new_emails

    async def sync(self, account: str = "default") -> tuple[SyncResult, list[NormalizedEmail]]:
        """Perform synchronization of new messages using initial lookback or incremental history.
        
        Returns:
            Tuple of (SyncResult summary, list of newly normalized emails).
        """
        async with self._sync_lock:
            # 1. Fetch user profile for latest state
            try:
                profile = await self.client.get_profile(account=account)
            except Exception as exc:
                logger.error("sync_get_profile_failed", error=str(exc))
                return (
                    SyncResult(
                        synced_count=0,
                        new_count=0,
                        skipped_count=0,
                        failed_count=1,
                        account_email=account,
                        errors=[str(exc)],
                    ),
                    [],
                )

            account_email = profile.email_address or account
            logger.info("gmail_sync_started", account=account_email)

            # 2. Load sync state
            state = await self.storage.get_sync_state(account_email)
            now_utc = datetime.now(timezone.utc)
            sync_state = state or EmailSyncState(account_email=account_email)
            sync_state.status = "syncing"
            sync_state.error_message = None
            await self.storage.save_sync_state(sync_state)

            result = SyncResult(account_email=account_email)
            new_emails: list[NormalizedEmail] = []

            try:
                if state is None or state.last_history_id is None:
                    # Initial synchronization via lookback query
                    cutoff = now_utc - timedelta(days=self.lookback_days)
                    epoch_sec = int(cutoff.timestamp())
                    query = f"after:{epoch_sec}"
                    logger.info("gmail_initial_sync", lookback_days=self.lookback_days, cutoff=cutoff.isoformat())

                    stubs, _ = await self.client.list_messages(
                        query=query,
                        max_results=self.max_messages,
                        account=account,
                    )
                    result.synced_count = len(stubs)
                    new_emails = await self._fetch_and_persist_messages(
                        stubs, account_email, account, result
                    )
                    latest_history_id = profile.history_id

                else:
                    # Incremental synchronization via users.history.list
                    start_history_id = state.last_history_id
                    logger.info("gmail_incremental_history_sync", start_history_id=start_history_id)
                    added_stubs: list[dict[str, Any]] = []
                    deleted_ids: set[str] = set()
                    latest_history_id = profile.history_id

                    try:
                        page_token: str | None = None
                        while True:
                            hist_resp = await self.client.get_history(
                                start_history_id=start_history_id,
                                max_results=min(self.max_messages, 100),
                                page_token=page_token,
                                account=account,
                            )
                            if "historyId" in hist_resp:
                                latest_history_id = str(hist_resp["historyId"])

                            for record in hist_resp.get("history", []):
                                for item in record.get("messagesAdded", []):
                                    msg = item.get("message")
                                    if msg and msg.get("id"):
                                        added_stubs.append(msg)
                                for item in record.get("messagesDeleted", []):
                                    msg = item.get("message")
                                    if msg and msg.get("id"):
                                        deleted_ids.add(msg["id"])

                            page_token = hist_resp.get("nextPageToken")
                            if not page_token or len(added_stubs) >= self.max_messages:
                                break

                    except GmailHistoryExpiredError:
                        # History cursor expired (HTTP 404): fall back to bounded query reinitialization
                        logger.warning(
                            "gmail_history_cursor_expired_reinitializing",
                            start_history_id=start_history_id,
                        )
                        cutoff = (state.last_synced_at or (now_utc - timedelta(days=self.lookback_days))) - timedelta(seconds=120)
                        query = f"after:{int(cutoff.timestamp())}"
                        added_stubs, _ = await self.client.list_messages(
                            query=query,
                            max_results=self.max_messages,
                            account=account,
                        )
                        latest_history_id = profile.history_id

                    # Handle deleted messages
                    for del_id in deleted_ids:
                        await self.storage.delete_email(del_id)
                        logger.debug("gmail_sync_deleted_message_processed", msg_id=del_id)

                    result.synced_count = len(added_stubs)
                    new_emails = await self._fetch_and_persist_messages(
                        added_stubs, account_email, account, result
                    )

                # Cursor safety: advance last_history_id and last_synced_at ONLY after processing succeeds
                sync_state.last_synced_at = now_utc
                sync_state.last_history_id = latest_history_id or profile.history_id
                sync_state.total_messages_synced += result.new_count
                sync_state.status = "idle"
                sync_state.error_message = None
                await self.storage.save_sync_state(sync_state)

                logger.info(
                    "gmail_sync_completed",
                    account=account_email,
                    new=result.new_count,
                    skipped=result.skipped_count,
                    failed=result.failed_count,
                    latest_history_id=sync_state.last_history_id,
                )

            except Exception as sync_exc:
                logger.error("gmail_sync_aborted", error=str(sync_exc))
                sync_state.status = "error"
                sync_state.error_message = str(sync_exc)
                await self.storage.save_sync_state(sync_state)
                result.errors.append(str(sync_exc))
                result.failed_count += 1

            return result, new_emails

