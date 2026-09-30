"""Unit tests for MacKeychainKeyProvider with mocked keyring for cross-platform/CI execution."""

import base64
import os
from unittest.mock import MagicMock, patch
import pytest

from core.memory.crypto import CryptoError, MacKeychainKeyProvider, MemoryEncryptor


@pytest.mark.asyncio
async def test_mocked_keychain_create_and_retrieve() -> None:
    fake_storage: dict[tuple[str, str], str] = {}

    def fake_get_password(service: str, username: str) -> str | None:
        return fake_storage.get((service, username))

    def fake_set_password(service: str, username: str, password: str) -> None:
        fake_storage[(service, username)] = password

    def fake_delete_password(service: str, username: str) -> None:
        fake_storage.pop((service, username), None)

    with patch("keyring.get_password", side_effect=fake_get_password), \
         patch("keyring.set_password", side_effect=fake_set_password), \
         patch("keyring.delete_password", side_effect=fake_delete_password):

        provider1 = MacKeychainKeyProvider(service_name="test.svc", username="user1")
        key1 = await provider1.get_or_create_memory_key()
        assert len(key1) == 32

        # Stored in mock keyring
        assert ("test.svc", "user1") in fake_storage

        # Roundtrip encrypt
        enc = MemoryEncryptor.encrypt("secret_data", key1)

        # Provider 2 recovers key from keyring
        provider2 = MacKeychainKeyProvider(service_name="test.svc", username="user1")
        key2 = await provider2.get_or_create_memory_key()
        assert key1 == key2

        decrypted = MemoryEncryptor.decrypt(enc, key2)
        assert decrypted == "secret_data"

        # Cleanup
        assert provider2.delete_key() is True
        assert ("test.svc", "user1") not in fake_storage


@pytest.mark.asyncio
async def test_mocked_keychain_error_and_fallback() -> None:
    with patch("keyring.get_password", side_effect=RuntimeError("Keychain locked")):
        # Without fallback -> raises CryptoError
        strict_prov = MacKeychainKeyProvider(fallback_to_memory=False)
        with pytest.raises(CryptoError, match="Failed to access macOS Keychain"):
            await strict_prov.get_or_create_memory_key()

        # With fallback -> generates fallback key
        fallback_prov = MacKeychainKeyProvider(fallback_to_memory=True)
        key = await fallback_prov.get_or_create_memory_key()
        assert len(key) == 32
