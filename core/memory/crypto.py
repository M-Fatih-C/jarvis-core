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
    def derive_fingerprint_key(key: bytes) -> bytes:
        """Derive an HMAC key from the master memory key for private fingerprinting."""
        import hashlib
        return hashlib.sha256(key + b":jarvis_memory_fingerprint_salt").digest()

    @staticmethod
    def encrypt(
        plaintext: str,
        key: bytes,
        key_version: int = 1,
        associated_data: bytes | None = None,
    ) -> dict[str, Any]:
        """Encrypt plaintext into AES-256-GCM ciphertext with random 96-bit nonce.
        
        Args:
            plaintext: Cleartext string to encrypt.
            key: 32-byte AES key.
            key_version: Incremental version integer.
            associated_data: Optional AAD bytes (e.g. record_id:schema_version) bound to ciphertext.
            
        Returns:
            Dictionary with ciphertext, nonce, algorithm, and key_version.
        """
        if len(key) != 32:
            raise CryptoError(f"Invalid key length: expected 32 bytes, got {len(key)}")

        try:
            aesgcm = AESGCM(key)
            nonce = os.urandom(12)  # 96 bits recommended for GCM
            data_bytes = plaintext.encode("utf-8")
            ciphertext_bytes = aesgcm.encrypt(nonce, data_bytes, associated_data)

            payload = {
                "ciphertext": base64.b64encode(ciphertext_bytes).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "algorithm": "AES-256-GCM",
                "key_version": key_version,
            }
            if associated_data is not None:
                payload["aad_bound"] = True
            return payload
        except Exception as exc:
            logger.error("encryption_failed", error=str(exc))
            raise CryptoError(f"Encryption failed: {exc}") from exc

    @classmethod
    def decrypt_raw(
        cls,
        encrypted_payload: dict[str, Any] | str,
        key: bytes,
        associated_data: bytes | None = None,
    ) -> str:
        """Decrypt AES-256-GCM payload and return raw decrypted string."""
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

            # Determine AAD: if not explicitly provided, derive from bound payload metadata if available
            aad_to_try = associated_data
            if aad_to_try is None and payload.get("aad_bound") and payload.get("record_id"):
                rec_id = payload["record_id"]
                sch_ver = payload.get("schema_version", 1)
                aad_to_try = f"{rec_id}:{sch_ver}".encode("utf-8")

            # Try decrypting with aad_to_try first
            decrypted_bytes: bytes
            try:
                decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, aad_to_try)
            except Exception:
                # Fallback to None if not aad_bound or if trying with aad failed
                if aad_to_try is not None:
                    decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, None)
                else:
                    raise

            return decrypted_bytes.decode("utf-8")
        except Exception as exc:
            logger.error("decryption_failed", error=str(exc))
            raise CryptoError(f"Decryption failed or invalid key/tag: {exc}") from exc

    @classmethod
    def decrypt(
        cls,
        encrypted_payload: dict[str, Any] | str,
        key: bytes,
        associated_data: bytes | None = None,
    ) -> str:
        """Decrypt AES-256-GCM payload. If unified private payload, unwrap content string."""
        raw_str = cls.decrypt_raw(encrypted_payload, key, associated_data=associated_data)
        try:
            parsed = json.loads(raw_str)
            if isinstance(parsed, dict) and "content" in parsed and "structured" in parsed:
                return parsed["content"] or ""
        except Exception:
            pass
        return raw_str

    @classmethod
    def encrypt_private_payload(
        cls,
        data: dict[str, Any],
        key: bytes,
        record_id: Any,
        schema_version: int = 1,
    ) -> dict[str, Any]:
        """Encrypt all sensitive attributes into a single unified AES-256-GCM payload."""
        plaintext = json.dumps(data, ensure_ascii=False)
        aad = f"{record_id}:{schema_version}".encode("utf-8") if record_id else None
        res = cls.encrypt(plaintext, key, key_version=1, associated_data=aad)
        if record_id:
            res["record_id"] = str(record_id)
        res["schema_version"] = schema_version
        return res

    @classmethod
    def decrypt_private_payload(
        cls,
        encrypted_payload: dict[str, Any] | str,
        key: bytes,
        record_id: Any = None,
        schema_version: int = 1,
    ) -> dict[str, Any]:
        """Decrypt unified payload and return structured data dictionary."""
        aad = f"{record_id}:{schema_version}".encode("utf-8") if record_id else None
        raw_str = cls.decrypt_raw(encrypted_payload, key, associated_data=aad)
        try:
            parsed = json.loads(raw_str)
            if isinstance(parsed, dict):
                return parsed
            return {"content": str(parsed)}
        except Exception:
            return {"content": raw_str}
