"""Client-side encryption using AES-256-GCM and macOS Keychain key management."""

from abc import ABC, abstractmethod
import base64
import json
import os
from typing import Any
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from core.logging.setup import get_logger

logger = get_logger("jarvis.memory.crypto")


class CryptoError(Exception):
    """Raised when encryption or decryption fails."""
    pass


class KeyProvider(ABC):
    """Abstract interface for retrieving or initializing memory encryption keys."""

    @abstractmethod
    async def get_or_create_memory_key(self) -> bytes:
        """Return a 256-bit (32-byte) symmetric encryption key."""
        pass


class InMemoryKeyProvider(KeyProvider):
    """In-memory key provider primarily for deterministic testing and isolated environments."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or os.urandom(32)

    async def get_or_create_memory_key(self) -> bytes:
        return self._key


class MacKeychainKeyProvider(KeyProvider):
    """macOS Keychain key provider using the native keyring service."""

    DEFAULT_SERVICE_NAME = "com.jarvis.memory"
    DEFAULT_USERNAME = "master_key"

    def __init__(
        self,
        service_name: str | None = None,
        username: str | None = None,
        fallback_to_memory: bool = False,
    ) -> None:
        self.service_name = service_name or self.DEFAULT_SERVICE_NAME
        self.username = username or self.DEFAULT_USERNAME
        self._fallback_to_memory = fallback_to_memory
        self._cached_key: bytes | None = None

    async def get_or_create_memory_key(self) -> bytes:
        if self._cached_key is not None:
            return self._cached_key

        try:
            import keyring
            key_b64 = keyring.get_password(self.service_name, self.username)
            if key_b64 is None:
                new_key = os.urandom(32)
                key_b64 = base64.b64encode(new_key).decode("ascii")
                keyring.set_password(self.service_name, self.username, key_b64)
                self._cached_key = new_key
                logger.info("keychain_key_initialized", service=self.service_name)
            else:
                self._cached_key = base64.b64decode(key_b64.encode("ascii"))
            return self._cached_key
        except Exception as exc:
            logger.warning("keychain_access_failed", error=str(exc))
            if self._fallback_to_memory:
                logger.info("falling_back_to_in_memory_key")
                self._cached_key = os.urandom(32)
                return self._cached_key
            raise CryptoError(f"Failed to access macOS Keychain: {exc}") from exc

    def delete_key(self) -> bool:
        """Clean up key from Keychain (useful for isolated acceptance tests)."""
        try:
            import keyring
            keyring.delete_password(self.service_name, self.username)
            self._cached_key = None
            return True
        except Exception:
            return False


class MemoryEncryptor:
    """Handles AES-256-GCM client-side encryption and decryption."""

    @staticmethod
    def encrypt(plaintext: str, key: bytes, key_version: int = 1) -> dict[str, Any]:
        """Encrypt plaintext into AES-256-GCM ciphertext with random 96-bit nonce.
        
        Args:
            plaintext: Cleartext string to encrypt.
            key: 32-byte AES key.
            key_version: Incremental version integer.
            
        Returns:
            Dictionary with ciphertext, nonce, algorithm, and key_version.
        """
        if len(key) != 32:
            raise CryptoError(f"Invalid key length: expected 32 bytes, got {len(key)}")

        try:
            aesgcm = AESGCM(key)
            nonce = os.urandom(12)  # 96 bits recommended for GCM
            data_bytes = plaintext.encode("utf-8")
            ciphertext_bytes = aesgcm.encrypt(nonce, data_bytes, None)

            return {
                "ciphertext": base64.b64encode(ciphertext_bytes).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "algorithm": "AES-256-GCM",
                "key_version": key_version,
            }
        except Exception as exc:
            logger.error("encryption_failed", error=str(exc))
            raise CryptoError(f"Encryption failed: {exc}") from exc

    @staticmethod
    def decrypt(encrypted_payload: dict[str, Any] | str, key: bytes) -> str:
        """Decrypt AES-256-GCM payload.
        
        Args:
            encrypted_payload: Dict or JSON string containing ciphertext, nonce, and algorithm.
            key: 32-byte AES key.
            
        Returns:
            Decrypted plaintext string.
        """
        if len(key) != 32:
            raise CryptoError(f"Invalid key length: expected 32 bytes, got {len(key)}")

        if isinstance(encrypted_payload, str):
            try:
                payload = json.loads(encrypted_payload)
            except json.JSONDecodeError as err:
                raise CryptoError("Encrypted payload is not valid JSON") from err
        else:
            payload = encrypted_payload

        try:
            ciphertext = base64.b64decode(payload["ciphertext"].encode("ascii"))
            nonce = base64.b64decode(payload["nonce"].encode("ascii"))
            aesgcm = AESGCM(key)
            decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, None)
            return decrypted_bytes.decode("utf-8")
        except Exception as exc:
            # Avoid logging raw payload
            logger.error("decryption_failed", error=str(exc))
            raise CryptoError(f"Decryption failed or invalid key/tag: {exc}") from exc
