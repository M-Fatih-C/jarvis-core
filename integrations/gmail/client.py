"""Async Gmail REST API client with bounded retries, quota handling, and read-only enforcement."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
from core.logging.setup import get_logger
from integrations.gmail.auth import GmailOAuthManager
from integrations.gmail.exceptions import (
    GmailAuthError,
    GmailHistoryExpiredError,
    GmailIntegrationError,
    GmailMessageNotFoundError,
    GmailNetworkError,
    GmailQuotaExceededError,
)
from integrations.gmail.models import GmailAccountProfile

logger = get_logger("jarvis.gmail.client")

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailClient:
    """Async client for Gmail v1 REST API with automatic token management and quota backoff."""

    def __init__(
        self,
        auth_manager: GmailOAuthManager,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
        base_backoff_seconds: float = 1.0,
    ) -> None:
        self.auth_manager = auth_manager
        self._http_client = http_client
        self.max_retries = max_retries
        self.base_backoff = base_backoff_seconds

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        return httpx.AsyncClient(timeout=20.0)

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        account: str = "default",
    ) -> dict[str, Any]:
        """Perform authenticated HTTP request with retry on transient network/rate errors."""
        url = f"{GMAIL_API_BASE}{endpoint}" if endpoint.startswith("/") else f"{GMAIL_API_BASE}/{endpoint}"
        retries = 0
        refreshed_auth = False

        while True:
            token = await self.auth_manager.get_valid_access_token(account=account)
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }

            client = self._get_client()
            should_close = self._http_client is None

            try:
                resp = await client.request(method, url, headers=headers, params=params)

                # Handle 401 Unauthorized (attempt token refresh once)
                if resp.status_code == 401 and not refreshed_auth:
                    logger.warning("gmail_unauthorized_attempting_refresh")
                    refreshed_auth = True
                    tokens = self.auth_manager.token_store.load_tokens(account)
                    if tokens and tokens.refresh_token:
                        new_tokens = await self.auth_manager.refresh_access_token(tokens.refresh_token)
                        self.auth_manager.token_store.save_tokens(account, new_tokens)
                        continue
                    raise GmailAuthError("Gmail authentication expired and cannot be refreshed.")

                # Handle 404 Not Found
                if resp.status_code == 404:
                    if endpoint.startswith("/history") or "startHistoryId" in (params or {}):
                        raise GmailHistoryExpiredError(f"Gmail history cursor expired for {endpoint}")
                    raise GmailMessageNotFoundError(f"Requested Gmail resource not found: {endpoint}")

                # Handle 429 Too Many Requests
                if resp.status_code == 429:
                    if retries >= self.max_retries:
                        raise GmailQuotaExceededError("Gmail API rate limit exceeded after maximum retries.")
                    delay = self.base_backoff * (2 ** retries) + random.uniform(0.1, 0.5)
                    logger.warning("gmail_rate_limited", delay=delay, attempt=retries + 1)
                    await asyncio.sleep(delay)
                    retries += 1
                    continue

                # Handle 5xx Transient Server Errors
                if resp.status_code in (500, 502, 503, 504):
                    if retries >= self.max_retries:
                        raise GmailIntegrationError(f"Google API server error ({resp.status_code}) after retries.")
                    delay = self.base_backoff * (2 ** retries) + random.uniform(0.1, 0.5)
                    logger.warning("gmail_server_error_retry", status=resp.status_code, delay=delay)
                    await asyncio.sleep(delay)
                    retries += 1
                    continue

                resp.raise_for_status()
                return resp.json()

            except httpx.RequestError as exc:
                if retries >= self.max_retries:
                    raise GmailNetworkError(f"Network error accessing Gmail API: {exc}") from exc
                delay = self.base_backoff * (2 ** retries) + random.uniform(0.1, 0.5)
                logger.warning("gmail_network_retry", error=str(exc), delay=delay)
                await asyncio.sleep(delay)
                retries += 1
            finally:
                if should_close:
                    await client.aclose()

    async def get_profile(self, account: str = "default") -> GmailAccountProfile:
        """Fetch the authenticated user's Gmail profile."""
        data = await self._request("GET", "/profile", account=account)
        return GmailAccountProfile(
            email_address=data.get("emailAddress", ""),
            messages_total=int(data.get("messagesTotal", 0)),
            threads_total=int(data.get("threadsTotal", 0)),
            history_id=str(data.get("historyId", "")),
        )

    async def list_messages(
        self,
        query: str | None = None,
        max_results: int = 25,
        page_token: str | None = None,
        account: str = "default",
    ) -> tuple[list[dict[str, str]], str | None]:
        """List messages matching an optional search query.
        
        Returns:
            Tuple of (list of message stubs [{"id": ..., "threadId": ...}], next_page_token)
        """
        params: dict[str, Any] = {"maxResults": min(max_results, 100)}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token

        data = await self._request("GET", "/messages", params=params, account=account)
        messages = data.get("messages", [])
        next_page = data.get("nextPageToken")
        return messages, next_page

    async def get_message(
        self,
        message_id: str,
        format_type: str = "full",
        account: str = "default",
    ) -> dict[str, Any]:
        """Fetch a single message by ID in full format."""
        params = {"format": format_type}
        return await self._request("GET", f"/messages/{message_id}", params=params, account=account)

    async def get_history(
        self,
        start_history_id: str,
        max_results: int = 100,
        page_token: str | None = None,
        account: str = "default",
    ) -> dict[str, Any]:
        """Fetch history records since start_history_id for incremental sync."""
        params: dict[str, Any] = {
            "startHistoryId": start_history_id,
            "maxResults": min(max_results, 500),
            "historyTypes": ["messageAdded", "messageDeleted"],
        }
        if page_token:
            params["pageToken"] = page_token
        return await self._request("GET", "/history", params=params, account=account)
