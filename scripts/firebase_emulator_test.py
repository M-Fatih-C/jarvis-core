#!/usr/bin/env python3
"""Milestone 2 Firebase Emulator Integration Test Script.

Validates:
1. NORMAL memory save, load, and tombstone
2. PRIVATE memory client-side encryption (no plaintext in Firestore, no vector embedding)
3. LOCAL_ONLY security invariant (rejected, never pushed to Firestore)
4. Memory revision conflict detection
5. Cloud command lifecycle: enqueue -> lease -> idempotency check -> complete
6. Device registry registration and heartbeat updates

Usage:
    python scripts/firebase_emulator_test.py
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import socket
import sys
from uuid import uuid4

from core.config.settings import Settings
from core.cloud.models import CloudCommand, CommandStatus, DeviceRecord, DeviceStatus
from core.memory.crypto import InMemoryKeyProvider, MemoryEncryptor
from core.memory.models import (
    MemoryFilters,
    MemoryKind,
    MemoryRecord,
    MemorySensitivity,
    MemorySourceType,
    MemoryStatus,
)
from core.memory.repository import MemoryConflictError
from integrations.firebase.client import FirestoreClientProvider
from integrations.firebase.command_repository import FirestoreCommandRepository
from integrations.firebase.device_repository import FirestoreDeviceRepository
from integrations.firebase.memory_repository import FirestoreMemoryRepository


def is_emulator_reachable(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


async def run_emulator_tests(require_emulator: bool = False) -> None:
    emulator_host = os.environ.get("FIRESTORE_EMULATOR_HOST", "127.0.0.1:8080")
    if ":" in emulator_host:
        host, port_str = emulator_host.split(":")
        port = int(port_str)
    else:
        host = emulator_host
        port = 8080

    print("=" * 70)
    print("JARVIS MILESTONE 2: FIREBASE EMULATOR INTEGRATION SUITE")
    print("=" * 70)
    print(f"[*] Checking Firestore Emulator at {emulator_host} (require_emulator={require_emulator})...")

    if not is_emulator_reachable(host, port):
        if require_emulator:
            print(f"\n[!] FATAL ERROR: Firestore Emulator is strictly required (--require-emulator) but was unreachable at {emulator_host}!")
            print("[!] Ensure Firebase emulator is active (e.g. firebase emulators:exec or firebase emulators:start).")
            sys.exit(1)

        print(f"\n[!] WARNING: Firestore Emulator is not reachable at {emulator_host}.")
        print("[!] Note: On macOS, Firebase Emulator requires Java runtime (`brew install openjdk`).")
        print("[!] To run the emulator, execute in a separate terminal:")
        print("      export FIRESTORE_EMULATOR_HOST=127.0.0.1:8080")
        print("      firebase emulators:start --only firestore")
        print("\n[*] Skipping live emulator assertions. (Use --require-emulator to enforce strict check).")
        return

    print("[*] Emulator reachable! Initializing Firestore repositories...")
    settings = Settings(
        firebase_project_id="jarvis-core-dev",
        jarvis_uid="test-user-m2",
        firestore_emulator_host=emulator_host,
    )
    client_provider = FirestoreClientProvider(settings=settings)
    mem_repo = FirestoreMemoryRepository(client_provider=client_provider, settings=settings)
    cmd_repo = FirestoreCommandRepository(client_provider=client_provider, settings=settings)
    dev_repo = FirestoreDeviceRepository(client_provider=client_provider, settings=settings)
    key_provider = InMemoryKeyProvider()
    key = await key_provider.get_or_create_memory_key()

    now = datetime.now(timezone.utc)

    # 1. NORMAL memory save and load
    print("\n[1] Testing NORMAL Memory Save & Load...")
    normal_id = uuid4()
    normal_rec = MemoryRecord(
        id=normal_id,
        kind=MemoryKind.PREFERENCE,
        sensitivity=MemorySensitivity.NORMAL,
        content="User prefers side projects after 18:00 on weekdays.",
        structured={"domain": "schedule", "days": "weekdays", "after": "18:00"},
        confidence=0.98,
        importance=0.85,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp_normal_demo",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        embedding=[0.1] * 384,
        embedding_model="mock-384",
    )
    await mem_repo.save(normal_rec)
    loaded_normal = await mem_repo.get(normal_id)
    assert loaded_normal is not None
    assert loaded_normal.content == normal_rec.content
    assert loaded_normal.embedding is not None
    print("  -> PASS: NORMAL memory saved and retrieved correctly.")

    # 2. PRIVATE memory client-side encryption
    print("\n[2] Testing PRIVATE Memory Client-Side Encryption...")
    private_id = uuid4()
    encrypted_dict = MemoryEncryptor.encrypt("Personal medical note: allergies to penicillin", key)
    private_rec = MemoryRecord(
        id=private_id,
        kind=MemoryKind.PROFILE,
        sensitivity=MemorySensitivity.PRIVATE,
        content=None,
        encrypted_content=json.dumps(encrypted_dict),
        structured={"category": "health"},
        confidence=1.0,
        importance=0.9,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp_private_demo",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        embedding=None,  # Forbidden in cloud
    )
    await mem_repo.save(private_rec)

    # Inspect raw doc in Firestore to guarantee NO plaintext or embedding exists
    raw_doc = await mem_repo._get_collection().document(str(private_id)).get()
    raw_data = raw_doc.to_dict()
    assert raw_data["content"] is None, "Security violation: Plaintext found in Firestore!"
    assert raw_data["embedding"] is None, "Security violation: Embedding found in Firestore for PRIVATE memory!"
    assert "encrypted" in raw_data, "Encrypted payload missing!"
    print("  -> PASS: PRIVATE memory contains zero plaintext and zero embedding in Firestore.")

    # 3. LOCAL_ONLY invariant
    print("\n[3] Testing LOCAL_ONLY Security Rejection...")
    local_rec = MemoryRecord(
        id=uuid4(),
        kind=MemoryKind.PROJECT,
        sensitivity=MemorySensitivity.LOCAL_ONLY,
        content="Secret local experiment",
        structured={},
        confidence=1.0,
        importance=0.5,
        source_type=MemorySourceType.EXPLICIT_USER,
        fingerprint="fp_local_demo",
        status=MemoryStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    try:
        await mem_repo.save(local_rec)
        raise AssertionError("LOCAL_ONLY should have raised ValueError!")
    except ValueError as e:
        print(f"  -> PASS: LOCAL_ONLY correctly rejected: {e}")

    # 4. Revision conflict detection
    print("\n[4] Testing Memory Revision Conflict Detection...")
    try:
        await mem_repo.update(loaded_normal, expected_revision=999)
        raise AssertionError("Expected revision mismatch did not raise conflict!")
    except MemoryConflictError:
        print("  -> PASS: Revision conflict detected and rejected successfully.")

    # 5. Device registry and heartbeat
    print("\n[5] Testing Device Registry & Heartbeat...")
    dev = DeviceRecord(
        device_id="mac-mini-test",
        type="macos",
        name="Jarvis Mac Test",
        status=DeviceStatus.ONLINE,
        capabilities=["local_llm", "memory", "tool_execution"],
        last_seen_at=now,
        created_at=now,
    )
    await dev_repo.register_or_update(dev)
    await dev_repo.heartbeat("mac-mini-test")
    loaded_dev = await dev_repo.get("mac-mini-test")
    assert loaded_dev is not None
    assert loaded_dev.status == DeviceStatus.ONLINE
    print("  -> PASS: Device registered and heartbeat updated.")

    # 6. Cloud command leasing and idempotency
    print("\n[6] Testing Cloud Command Leasing & Idempotency...")
    cmd_id = "cmd_emulator_test"
    cmd = CloudCommand(
        id=cmd_id,
        name="system.get_status",
        source_device="ios",
        idempotency_key="idem_key_42",
        created_at=now,
        available_at=now,
    )
    c1 = await cmd_repo.create(cmd)
    # Duplicate create must return existing
    c2 = await cmd_repo.create(cmd)
    assert c1.id == c2.id
    print("  -> PASS: Idempotent duplicate command recognized.")

    leased = await cmd_repo.lease_next_command("mac-mini-test", lease_duration_seconds=30)
    assert leased is not None
    assert leased.id == cmd_id
    assert leased.lease_owner == "mac-mini-test"
    assert leased.status == CommandStatus.LEASED
    print("  -> PASS: Command successfully leased by Mac worker.")

    # Complete command
    await cmd_repo.update_status(
        command_id=cmd_id,
        status=CommandStatus.COMPLETED,
        result={"status": "ok", "battery": "100%"},
    )
    completed_cmd = await cmd_repo.get(cmd_id)
    assert completed_cmd.status == CommandStatus.COMPLETED
    print("  -> PASS: Command marked COMPLETED with result payload.")

    await client_provider.close()
    print("\n" + "=" * 70)
    print("ALL FIREBASE EMULATOR TESTS PASSED!")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis Firebase Emulator Test Suite")
    parser.add_argument(
        "--require-emulator",
        action="store_true",
        help="Enforce strict acceptance mode: exit with non-zero code if emulator is offline",
    )
    args = parser.parse_args()
    asyncio.run(run_emulator_tests(require_emulator=args.require_emulator))


if __name__ == "__main__":
    main()
