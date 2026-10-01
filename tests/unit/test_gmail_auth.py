"""Unit tests for Gmail OAuth 2.0 PKCE authentication, token lifecycle, and Keychain storage."""

import base64
from datetime import datetime, timezone
import hashlib
import json
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from core.config.settings import Settings
from integrations.gmail.auth import (
    GOOGLE_AUTH_URL,
    GOOGLE_REVOKE_URL,
    GOOGLE_TOKEN_URL,
    GmailOAuthManager,
    KeychainTokenStore,
)
from integrations.gmail.exceptions import (
    GmailAuthError,
    GmailNetworkError,
    GmailRevokedError,
    GmailTokenExpiredError,
)
from integrations.gmail.models import OAuthTokens


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        environment="test",
        gmail_client_id="test_client_id.apps.googleusercontent.com",
        gmail_client_secret="test_client_secret",
    )


@pytest.fixture
def in_memory_store() -> KeychainTokenStore:
    return KeychainTokenStore(service_name="test.jarvis.gmail", fallback_to_memory=True)


def test_pkce_generation() -> None:
    verifier, challenge = GmailOAuthManager.generate_pkce_pair()
    assert 43 <= len(verifier) <= 128
    # Validate challenge is SHA256 of verifier base64url encoded
    expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected_challenge = base64.urlsafe_b64encode(expected_digest).decode("ascii").rstrip("=")
    assert challenge == expected_challenge


def test_authorization_url_construction(test_settings: Settings, in_memory_store: KeychainTokenStore) -> None:
    mgr = GmailOAuthManager(settings=test_settings, token_store=in_memory_store)
    url = mgr.create_authorization_url(
        redirect_uri="http://127.0.0.1:8766",
        state="csrf_state_123",
        code_challenge="challenge_abc",
    )
    assert url.startswith(GOOGLE_AUTH_URL)
    assert "client_id=test_client_id" in url
    assert "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fgmail.readonly" in url
    assert "code_challenge=challenge_abc" in url
    assert "code_challenge_method=S256" in url
    assert "state=csrf_state_123" in url
    assert "access_type=offline" in url


@pytest.mark.asyncio
async def test_exchange_code_success(test_settings: Settings, in_memory_store: KeychainTokenStore) -> None:
    mock_resp = httpx.Response(
        status_code=200,
        json={
            "access_token": "ya29.test_access_token",
            "refresh_token": "1//test_refresh_token",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        },
    )
    transport = httpx.MockTransport(lambda req: mock_resp)
    async with httpx.AsyncClient(transport=transport) as http_client:
        mgr = GmailOAuthManager(settings=test_settings, token_store=in_memory_store, http_client=http_client)
        tokens = await mgr.exchange_code(
            code="test_auth_code",
            code_verifier="test_verifier",
            redirect_uri="http://127.0.0.1:8766",
        )
        assert tokens.access_token == "ya29.test_access_token"
        assert tokens.refresh_token == "1//test_refresh_token"
        assert not tokens.is_expired


@pytest.mark.asyncio
async def test_exchange_code_network_error(test_settings: Settings, in_memory_store: KeychainTokenStore) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        mgr = GmailOAuthManager(settings=test_settings, token_store=in_memory_store, http_client=http_client)
        with pytest.raises(GmailNetworkError, match="Network error during code exchange"):
            await mgr.exchange_code("code", "verifier", "http://127.0.0.1:8766")


@pytest.mark.asyncio
async def test_token_refresh_success(test_settings: Settings, in_memory_store: KeychainTokenStore) -> None:
    mock_resp = httpx.Response(
        status_code=200,
        json={
            "access_token": "ya29.new_refreshed_token",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    transport = httpx.MockTransport(lambda req: mock_resp)
    async with httpx.AsyncClient(transport=transport) as http_client:
        mgr = GmailOAuthManager(settings=test_settings, token_store=in_memory_store, http_client=http_client)
        new_tokens = await mgr.refresh_access_token("1//existing_refresh_token")
        assert new_tokens.access_token == "ya29.new_refreshed_token"
        assert new_tokens.refresh_token == "1//existing_refresh_token"


@pytest.mark.asyncio
async def test_token_refresh_revoked(test_settings: Settings, in_memory_store: KeychainTokenStore) -> None:
    mock_resp = httpx.Response(
        status_code=400,
        json={"error": "invalid_grant", "error_description": "Token has been expired or revoked."},
    )
    transport = httpx.MockTransport(lambda req: mock_resp)
    async with httpx.AsyncClient(transport=transport) as http_client:
        mgr = GmailOAuthManager(settings=test_settings, token_store=in_memory_store, http_client=http_client)
        with pytest.raises(GmailRevokedError, match="revoked or expired"):
            await mgr.refresh_access_token("1//revoked_token")


def test_keychain_token_store_lifecycle(in_memory_store: KeychainTokenStore) -> None:
    tokens = OAuthTokens(
        access_token="ya29.stored_token",
        refresh_token="1//refresh",
        expires_at=datetime.now(timezone.utc).timestamp() + 3600,
    )
    in_memory_store.save_tokens("user@test.com", tokens)

    loaded = in_memory_store.load_tokens("user@test.com")
    assert loaded is not None
    assert loaded.access_token == "ya29.stored_token"

    cleared = in_memory_store.clear_tokens("user@test.com")
    assert cleared is True
    assert in_memory_store.load_tokens("user@test.com") is None


@pytest.mark.asyncio
async def test_get_valid_access_token_auto_refresh(test_settings: Settings, in_memory_store: KeychainTokenStore) -> None:
    # Save expired tokens
    expired_tokens = OAuthTokens(
        access_token="ya29.expired_token",
        refresh_token="1//refresh_val",
        expires_at=datetime.now(timezone.utc).timestamp() - 100,  # Expired
    )
    in_memory_store.save_tokens("default", expired_tokens)

    refresh_resp = httpx.Response(
        status_code=200,
        json={"access_token": "ya29.fresh_token", "expires_in": 3600},
    )
    transport = httpx.MockTransport(lambda req: refresh_resp)
    async with httpx.AsyncClient(transport=transport) as http_client:
        mgr = GmailOAuthManager(settings=test_settings, token_store=in_memory_store, http_client=http_client)
        valid_token = await mgr.get_valid_access_token("default")
        assert valid_token == "ya29.fresh_token"

        # Verify updated in store
        persisted = in_memory_store.load_tokens("default")
        assert persisted is not None
        assert persisted.access_token == "ya29.fresh_token"


@pytest.mark.asyncio
async def test_revoke_authorization(test_settings: Settings, in_memory_store: KeychainTokenStore) -> None:
    tokens = OAuthTokens(
        access_token="ya29.to_revoke",
        refresh_token="1//refresh_to_revoke",
        expires_at=datetime.now(timezone.utc).timestamp() + 3600,
    )
    in_memory_store.save_tokens("default", tokens)

    transport = httpx.MockTransport(lambda req: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as http_client:
        mgr = GmailOAuthManager(settings=test_settings, token_store=in_memory_store, http_client=http_client)
        result = await mgr.revoke_authorization("default")
        assert result is True
        assert not mgr.is_connected("default")
