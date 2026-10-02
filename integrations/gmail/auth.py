"""Gmail OAuth 2.0 PKCE desktop authentication manager and Keychain token store."""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import secrets
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from core.config.settings import Settings, get_settings
from core.logging.setup import get_logger
from integrations.gmail.exceptions import (
    GmailAuthError,
    GmailNetworkError,
    GmailRevokedError,
    GmailTokenExpiredError,
)
from integrations.gmail.models import OAuthTokens

logger = get_logger("jarvis.gmail.auth")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class KeychainTokenStore:
    """Secure token persistence using macOS Keychain via keyring."""

    DEFAULT_SERVICE = "com.jarvis.gmail"

    def __init__(self, service_name: str | None = None, fallback_to_memory: bool = False) -> None:
        self.service_name = service_name or self.DEFAULT_SERVICE
        self._fallback_to_memory = fallback_to_memory
        self._memory_cache: dict[str, str] = {}

    def save_tokens(self, account: str, tokens: OAuthTokens) -> None:
        """Serialize and save tokens securely."""
        data = tokens.model_dump_json()
        try:
            import keyring
            keyring.set_password(self.service_name, account, data)
            logger.info("gmail_tokens_saved_keychain", service=self.service_name, account=account)
        except Exception as exc:
            logger.warning("keychain_save_failed", error=str(exc))
            if self._fallback_to_memory:
                self._memory_cache[account] = data
            else:
                raise GmailAuthError(f"Failed to save tokens in Keychain: {exc}") from exc

    def load_tokens(self, account: str) -> OAuthTokens | None:
        """Load and deserialize tokens from Keychain."""
        try:
            import keyring
            raw = keyring.get_password(self.service_name, account)
        except Exception as exc:
            logger.warning("keychain_read_failed", error=str(exc))
            if self._fallback_to_memory:
                raw = self._memory_cache.get(account)
            else:
                raise GmailAuthError(f"Failed to read tokens from Keychain: {exc}") from exc

        if not raw and self._fallback_to_memory:
            raw = self._memory_cache.get(account)

        if not raw:
            return None

        try:
            return OAuthTokens.model_validate_json(raw)
        except Exception as exc:
            logger.warning("keychain_token_parse_failed", error=str(exc))
            return None

    def clear_tokens(self, account: str) -> bool:
        """Remove tokens from Keychain and memory."""
        cleared = False
        try:
            import keyring
            keyring.delete_password(self.service_name, account)
            cleared = True
        except Exception:
            pass

        if account in self._memory_cache:
            del self._memory_cache[account]
            cleared = True

        logger.info("gmail_tokens_cleared", account=account)
        return cleared


class GmailOAuthManager:
    """Manages Google OAuth 2.0 PKCE flow, token lifecycle, and account connection."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        token_store: KeychainTokenStore | None = None,
        http_client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client_id = client_id or self.settings.gmail_client_id
        self.client_secret = client_secret or self.settings.gmail_client_secret
        self.token_store = token_store or KeychainTokenStore(
            fallback_to_memory=(self.settings.environment == "test")
        )
        self._http_client = http_client

    def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        return httpx.AsyncClient(timeout=15.0)

    @staticmethod
    def generate_pkce_pair() -> tuple[str, str]:
        """Generate (code_verifier, code_challenge) using S256."""
        # RFC 7636: 43 to 128 characters
        random_bytes = secrets.token_bytes(48)
        code_verifier = base64.urlsafe_b64encode(random_bytes).decode("ascii").rstrip("=")
        # S256 challenge
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        return code_verifier, code_challenge

    def create_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        code_challenge: str,
    ) -> str:
        """Build Google OAuth authorization URL for desktop PKCE flow."""
        if not self.client_id:
            raise GmailAuthError("GMAIL_CLIENT_ID is required for OAuth authorization.")

        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GMAIL_READONLY_SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(
        self,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> OAuthTokens:
        """Exchange authorization code for access and refresh tokens using PKCE."""
        if not self.client_id:
            raise GmailAuthError("GMAIL_CLIENT_ID is required to exchange authorization code.")

        payload = {
            "client_id": self.client_id,
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret

        client = self._get_client()
        try:
            resp = await client.post(GOOGLE_TOKEN_URL, data=payload)
            if resp.status_code == 400:
                err_data = resp.json()
                raise GmailAuthError(f"OAuth exchange failed: {err_data.get('error_description', resp.text)}")
            resp.raise_for_status()
            data = resp.json()

            expires_in = int(data.get("expires_in", 3600))
            expires_at = datetime.now(timezone.utc).timestamp() + expires_in

            tokens = OAuthTokens(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token"),
                token_type=data.get("token_type", "Bearer"),
                expires_in=expires_in,
                expires_at=expires_at,
                scope=data.get("scope", GMAIL_READONLY_SCOPE),
            )
            return tokens
        except httpx.RequestError as exc:
            raise GmailNetworkError(f"Network error during code exchange: {exc}") from exc
        finally:
            if self._http_client is None:
                await client.aclose()

    async def refresh_access_token(self, refresh_token: str) -> OAuthTokens:
        """Obtain a new access token using a refresh token."""
        if not self.client_id:
            raise GmailAuthError("GMAIL_CLIENT_ID is required for token refresh.")

        payload = {
            "client_id": self.client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }
        if self.client_secret:
            payload["client_secret"] = self.client_secret

        client = self._get_client()
        try:
            resp = await client.post(GOOGLE_TOKEN_URL, data=payload)
            if resp.status_code in (400, 401):
                err_data = resp.json()
                err_type = err_data.get("error", "")
                if err_type in ("invalid_grant", "revoked"):
                    raise GmailRevokedError("Gmail authorization has been revoked or expired.")
                raise GmailAuthError(f"Token refresh rejected: {err_data.get('error_description', resp.text)}")
            resp.raise_for_status()
            data = resp.json()

            expires_in = int(data.get("expires_in", 3600))
            expires_at = datetime.now(timezone.utc).timestamp() + expires_in

            tokens = OAuthTokens(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token") or refresh_token,
                token_type=data.get("token_type", "Bearer"),
                expires_in=expires_in,
                expires_at=expires_at,
                scope=data.get("scope", GMAIL_READONLY_SCOPE),
            )
            return tokens
        except httpx.RequestError as exc:
            raise GmailNetworkError(f"Network error refreshing token: {exc}") from exc
        finally:
            if self._http_client is None:
                await client.aclose()

    async def revoke_authorization(self, account: str = "default") -> bool:
        """Revoke OAuth tokens with Google and remove credentials from Keychain."""
        tokens = self.token_store.load_tokens(account)
        if not tokens:
            return True

        target_token = tokens.refresh_token or tokens.access_token
        client = self._get_client()
        revoked_remotely = False
        try:
            resp = await client.post(
                GOOGLE_REVOKE_URL,
                params={"token": target_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            revoked_remotely = resp.is_success
        except Exception as exc:
            logger.warning("token_revocation_network_error", error=str(exc))
        finally:
            if self._http_client is None:
                await client.aclose()

        self.token_store.clear_tokens(account)
        return True

    async def get_valid_access_token(self, account: str = "default") -> str:
        """Retrieve a valid access token from Keychain, refreshing it if expired."""
        tokens = self.token_store.load_tokens(account)
        if not tokens:
            raise GmailAuthError(f"No Gmail credentials found for account '{account}'. Authentication required.")

        if not tokens.is_expired:
            return tokens.access_token

        if not tokens.refresh_token:
            raise GmailTokenExpiredError("Access token expired and no refresh token is stored in Keychain.")

        logger.info("refreshing_expired_gmail_token", account=account)
        new_tokens = await self.refresh_access_token(tokens.refresh_token)
        self.token_store.save_tokens(account, new_tokens)
        return new_tokens.access_token

    def is_connected(self, account: str = "default") -> bool:
        """Check if active tokens exist in Keychain for the account."""
        tokens = self.token_store.load_tokens(account)
        return tokens is not None

    async def start_interactive_auth_flow(
        self,
        port: int = 8766,
        timeout: float = 180.0,
        account: str = "default",
        open_browser: bool = True,
    ) -> OAuthTokens:
        """Run interactive loopback OAuth 2.0 PKCE flow for desktop app."""
        if not self.client_id:
            raise GmailAuthError("GMAIL_CLIENT_ID must be configured for OAuth authentication.")

        verifier, challenge = self.generate_pkce_pair()
        state = secrets.token_urlsafe(32)
        redirect_uri = f"http://127.0.0.1:{port}"
        auth_url = self.create_authorization_url(redirect_uri, state, challenge)

        loop = asyncio.get_running_loop()
        code_future: asyncio.Future[str] = loop.create_future()

        async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                line = await reader.readline()
                request_line = line.decode("utf-8", errors="replace")
                parts = request_line.split()
                if len(parts) >= 2 and parts[0] == "GET":
                    parsed = urlparse(parts[1])
                    query = parse_qs(parsed.query)

                    if "error" in query:
                        err = query["error"][0]
                        if not code_future.done():
                            code_future.set_exception(GmailAuthError(f"OAuth error: {err}"))
                        body = "<html><body><h2>Authorization Denied</h2><p>You may close this tab.</p></body></html>"
                        writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\n\r\n" + body.encode("utf-8"))
                    elif "code" in query and "state" in query:
                        ret_state = query["state"][0]
                        ret_code = query["code"][0]
                        if ret_state != state:
                            if not code_future.done():
                                code_future.set_exception(GmailAuthError("CSRF State mismatch!"))
                            body = "<html><body><h2>CSRF State Mismatch</h2><p>Security validation failed.</p></body></html>"
                            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/html; charset=utf-8\r\n\r\n" + body.encode("utf-8"))
                        else:
                            if not code_future.done():
                                code_future.set_result(ret_code)
                            body = "<html><body style='font-family: system-ui; text-align: center; padding: 40px;'><h2>\u2705 Jarvis Gmail Yetkilendirmesi Ba\u015far\u0131l\u0131!</h2><p>Bu pencereyi kapatabilirsiniz.</p></body></html>"
                            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n" + body.encode("utf-8"))
                    else:
                        writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
                await writer.drain()
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        server = await asyncio.start_server(_handle_client, "127.0.0.1", port)
        logger.info("oauth_loopback_server_listening", port=port)

        print("\n" + "=" * 70)
        print("GOOGLE OAUTH 2.0 AUTHORIZATION REQUIRED")
        print("=" * 70)
        print("Please visit the following URL to connect your Gmail account:")
        print(f"\n{auth_url}\n")
        print("=" * 70 + "\n")

        if open_browser:
            try:
                import webbrowser
                webbrowser.open(auth_url)
            except Exception:
                pass

        try:
            auth_code = await asyncio.wait_for(code_future, timeout=timeout)
        except asyncio.TimeoutError:
            raise GmailAuthError(f"OAuth authorization timed out after {timeout} seconds.")
        finally:
            server.close()
            await server.wait_closed()

        tokens = await self.exchange_code(
            code=auth_code,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
        )
        self.token_store.save_tokens(account, tokens)
        logger.info("oauth_authorization_completed_successfully", account=account)
        return tokens

