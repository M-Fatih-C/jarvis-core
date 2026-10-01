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

    async def sync(self, account: str = "default") -> tuple[SyncResult, list[NormalizedEmail]]:
        """Perform synchronization of new messages.
        
        Returns:
            Tuple of (SyncResult summary, list of newly normalized emails).
        """
        async with self._sync_lock:
            # 1. Fetch user profile
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

            # Determine query for initial vs incremental sync
            if state is None or state.last_synced_at is None:
                # Initial sync: lookback_days
                cutoff = now_utc - timedelta(days=self.lookback_days)
                epoch_sec = int(cutoff.timestamp())
                query = f"after:{epoch_sec}"
                is_initial = True
                logger.info("gmail_initial_sync", lookback_days=self.lookback_days, cutoff=cutoff.isoformat())
            else:
                # Incremental sync: last_synced_at with 1-minute overlap buffer for clock skew
                cutoff = state.last_synced_at - timedelta(seconds=60)
                epoch_sec = int(cutoff.timestamp())
                query = f"after:{epoch_sec}"
                is_initial = False
                logger.info("gmail_incremental_sync", after=cutoff.isoformat())

            # Mark state as syncing
            sync_state = state or EmailSyncState(account_email=account_email)
            sync_state.status = "syncing"
            sync_state.error_message = None
            await self.storage.save_sync_state(sync_state)

            result = SyncResult(account_email=account_email)
            new_emails: list[NormalizedEmail] = []

            try:
                # 3. List messages
                message_stubs, _ = await self.client.list_messages(
                    query=query,
                    max_results=self.max_messages,
                    account=account,
                )

                result.synced_count = len(message_stubs)
                logger.info("gmail_sync_messages_found", count=len(message_stubs))

                # 4. Fetch, normalize, and persist each message
                for stub in message_stubs:
                    msg_id = stub.get("id", "")
                    if not msg_id:
                        continue

                    # Deduplication check: skip if already in local storage
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
                        # Message was deleted or inaccessible in the meantime
                        logger.warning("gmail_message_deleted_or_missing", msg_id=msg_id)
                        result.skipped_count += 1
                    except Exception as msg_exc:
                        logger.error("gmail_message_processing_failed", msg_id=msg_id, error=str(msg_exc))
                        result.failed_count += 1
                        result.errors.append(f"Message {msg_id}: {msg_exc}")

                # 5. Update persistent sync state
                sync_state.last_synced_at = now_utc
                sync_state.last_history_id = profile.history_id
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
                )

            except Exception as sync_exc:
                logger.error("gmail_sync_aborted", error=str(sync_exc))
                sync_state.status = "error"
                sync_state.error_message = str(sync_exc)
                await self.storage.save_sync_state(sync_state)
                result.errors.append(str(sync_exc))
                result.failed_count += 1

            return result, new_emails
