#!/usr/bin/env python3
"""Milestone 2.1 macOS Keychain Acceptance Verification Script.

Tests the full production key management lifecycle using macOS native Keychain:
1. Initialize MacKeychainKeyProvider with dedicated isolated acceptance namespace:
   service="com.jarvis.acceptance_test", account="test_key"
2. Generate/store memory key in Keychain.
3. Encrypt a synthetic test payload using AES-256-GCM.
4. Destroy the provider instance and clear in-memory caches.
5. Instantiate a completely fresh provider instance.
6. Retrieve the key from macOS Keychain.
7. Successfully decrypt and verify the ciphertext.
8. Clean up only the dedicated test key from the Keychain.

Security Invariants:
- Never print the raw encryption key.
- Never log sensitive cleartext payloads.
- Touch only the dedicated acceptance test account namespace.

Usage:
    python scripts/keychain_acceptance.py
"""

from __future__ import annotations

import asyncio
import sys

from core.memory.crypto import MacKeychainKeyProvider, MemoryEncryptor


async def run_keychain_acceptance() -> None:
    print("=" * 70)
    print("JARVIS MILESTONE 2.1: MACOS KEYCHAIN ACCEPTANCE TEST")
    print("=" * 70)

    # 1. Setup isolated namespace
    test_service = "com.jarvis.acceptance_test"
    test_account = "acceptance_master_key"
    synthetic_payload = "SYNTHETIC_TEST_USER_CONFIDENTIAL_RECORD"

    print(f"[*] Target Keychain Service: {test_service}")
    print(f"[*] Target Keychain Account: {test_account}")

    provider1 = MacKeychainKeyProvider(
        service_name=test_service,
        username=test_account,
        fallback_to_memory=False,
    )

    try:
        # 2. Generate/fetch key
        print("\n[Step 1] Creating/retrieving test encryption key from macOS Keychain...")
        key1 = await provider1.get_or_create_memory_key()
        assert len(key1) == 32, "Key must be exactly 32 bytes (256 bits)"
        print("  -> Key successfully retrieved from Keychain (Length: 32 bytes, Raw key: [REDACTED])")

        # 3. Encrypt synthetic payload
        print("\n[Step 2] Encrypting synthetic PRIVATE payload using AES-256-GCM...")
        enc_doc = MemoryEncryptor.encrypt(synthetic_payload, key1)
        assert "ciphertext" in enc_doc
        assert "nonce" in enc_doc
        assert enc_doc["algorithm"] == "AES-256-GCM"
        print(f"  -> Payload encrypted. Nonce length: {len(enc_doc['nonce'])}, Ciphertext: {enc_doc['ciphertext'][:16]}...")

        # 4. Destroy provider 1
        print("\n[Step 3] Destroying provider instance and purging local memory cache...")
        del provider1
        del key1

        # 5. Instantiate fresh provider
        print("\n[Step 4] Instantiating brand new MacKeychainKeyProvider instance...")
        provider2 = MacKeychainKeyProvider(
            service_name=test_service,
            username=test_account,
            fallback_to_memory=False,
        )

        # 6. Retrieve persisted key from Keychain
        print("\n[Step 5] Fetching key from macOS Keychain via new provider instance...")
        key2 = await provider2.get_or_create_memory_key()
        assert len(key2) == 32
        print("  -> Key retrieved from macOS Keychain (Length: 32 bytes, Raw key: [REDACTED])")

        # 7. Decrypt and verify
        print("\n[Step 6] Decrypting ciphertext using recovered Keychain key...")
        decrypted = MemoryEncryptor.decrypt(enc_doc, key2)
        assert decrypted == synthetic_payload, "Decrypted text does not match original synthetic payload!"
        print("  -> PASS: Decryption succeeded! Authentication tag verified by AES-GCM.")

        print("\n" + "=" * 70)
        print("MACOS KEYCHAIN ACCEPTANCE TEST PASSED!")
        print("=" * 70)

    finally:
        # 8. Clean up only the dedicated test key
        print("\n[*] Cleaning up dedicated test key from macOS Keychain...")
        cleanup_provider = MacKeychainKeyProvider(
            service_name=test_service,
            username=test_account,
        )
        cleaned = cleanup_provider.delete_key()
        if cleaned:
            print(f"  -> Successfully deleted test record '{test_account}' from '{test_service}'.")
        else:
            print("  -> Cleanup completed (or record was already removed).")


def main() -> None:
    if sys.platform != "darwin":
        print("[!] Note: scripts/keychain_acceptance.py is designed for macOS. Skipping on non-macOS.")
        sys.exit(0)
    asyncio.run(run_keychain_acceptance())


if __name__ == "__main__":
    main()
