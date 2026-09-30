"""Abstract repository interfaces and in-memory local implementations for commands and devices."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from core.cloud.models import CloudCommand, CommandStatus, DeviceRecord, DeviceStatus


class CommandConflictError(Exception):
    """Raised when an update to a cloud command encounters a concurrency conflict."""
    pass


class CommandRepository(ABC):
    """Abstract interface for managing cloud command queues."""

    @abstractmethod
    async def get(self, command_id: str) -> CloudCommand | None:
        pass

    @abstractmethod
    async def create(self, command: CloudCommand) -> CloudCommand:
        pass

    @abstractmethod
    async def update(self, command: CloudCommand, expected_revision: int | None = None) -> CloudCommand:
        pass

    @abstractmethod
    async def lease_next_command(self, worker_id: str, lease_duration_seconds: int = 60) -> CloudCommand | None:
        """Atomically claim the next eligible queued command."""
        pass

    @abstractmethod
    async def find_by_idempotency_key(self, idempotency_key: str) -> CloudCommand | None:
        pass

    @abstractmethod
    async def list(self, status: CommandStatus | None = None) -> list[CloudCommand]:
        pass


class DeviceRepository(ABC):
    """Abstract interface for tracking device fleet and heartbeats."""

    @abstractmethod
    async def register_or_update(self, device: DeviceRecord) -> DeviceRecord:
        pass

    @abstractmethod
    async def get(self, device_id: str) -> DeviceRecord | None:
        pass

    @abstractmethod
    async def list(self) -> list[DeviceRecord]:
        pass

    @abstractmethod
    async def heartbeat(self, device_id: str, status: DeviceStatus = DeviceStatus.ONLINE) -> bool:
        pass


class InMemoryCommandRepository(CommandRepository):
    """Thread-safe in-memory command queue repository."""

    def __init__(self) -> None:
        self._commands: dict[str, CloudCommand] = {}
        self._idempotency_map: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get(self, command_id: str) -> CloudCommand | None:
        async with self._lock:
            cmd = self._commands.get(command_id)
            return cmd.model_copy(deep=True) if cmd else None

    async def find_by_idempotency_key(self, idempotency_key: str) -> CloudCommand | None:
        async with self._lock:
            cmd_id = self._idempotency_map.get(idempotency_key)
            if cmd_id and cmd_id in self._commands:
                return self._commands[cmd_id].model_copy(deep=True)
            return None

    async def create(self, command: CloudCommand) -> CloudCommand:
        async with self._lock:
            if command.idempotency_key in self._idempotency_map:
                existing_id = self._idempotency_map[command.idempotency_key]
                return self._commands[existing_id].model_copy(deep=True)

            self._commands[command.id] = command.model_copy(deep=True)
            self._idempotency_map[command.idempotency_key] = command.id
            return command.model_copy(deep=True)

    async def update(self, command: CloudCommand, expected_revision: int | None = None) -> CloudCommand:
        async with self._lock:
            if command.id not in self._commands:
                raise ValueError(f"Command {command.id} not found")

            curr = self._commands[command.id]
            if expected_revision is not None and curr.revision != expected_revision:
                raise CommandConflictError(
                    f"Conflict for command {command.id}: expected {expected_revision}, got {curr.revision}"
                )

            command.revision = curr.revision + 1
            self._commands[command.id] = command.model_copy(deep=True)
            return command.model_copy(deep=True)

    async def lease_next_command(self, worker_id: str, lease_duration_seconds: int = 60) -> CloudCommand | None:
        async with self._lock:
            now = datetime.now(timezone.utc)
            eligible: list[CloudCommand] = []

            for cmd in self._commands.values():
                # Check expiration
                if cmd.expires_at and cmd.expires_at < now:
                    if cmd.status not in (CommandStatus.COMPLETED, CommandStatus.FAILED, CommandStatus.EXPIRED):
                        cmd.status = CommandStatus.EXPIRED
                    continue

                if cmd.status == CommandStatus.QUEUED and cmd.available_at <= now:
                    eligible.append(cmd)
                elif cmd.status in (CommandStatus.LEASED, CommandStatus.RUNNING):
                    # Check if lease expired
                    if cmd.lease_expires_at and cmd.lease_expires_at < now:
                        eligible.append(cmd)

            if not eligible:
                return None

            # Sort by available_at, then created_at
            eligible.sort(key=lambda c: (c.available_at, c.created_at))
            target = eligible[0]

            target.status = CommandStatus.LEASED
            target.lease_owner = worker_id
            target.lease_expires_at = now + timedelta(seconds=lease_duration_seconds)
            target.attempts += 1
            target.revision += 1

            self._commands[target.id] = target.model_copy(deep=True)
            return target.model_copy(deep=True)

    async def list(self, status: CommandStatus | None = None) -> list[CloudCommand]:
        async with self._lock:
            if status is None:
                return [c.model_copy(deep=True) for c in self._commands.values()]
            return [c.model_copy(deep=True) for c in self._commands.values() if c.status == status]


class InMemoryDeviceRepository(DeviceRepository):
    """Thread-safe in-memory device registry."""

    def __init__(self) -> None:
        self._devices: dict[str, DeviceRecord] = {}
        self._lock = asyncio.Lock()

    async def register_or_update(self, device: DeviceRecord) -> DeviceRecord:
        async with self._lock:
            device.last_seen_at = datetime.now(timezone.utc)
            self._devices[device.device_id] = device.model_copy(deep=True)
            return device.model_copy(deep=True)

    async def get(self, device_id: str) -> DeviceRecord | None:
        async with self._lock:
            dev = self._devices.get(device_id)
            return dev.model_copy(deep=True) if dev else None

    async def list(self) -> list[DeviceRecord]:
        async with self._lock:
            return [d.model_copy(deep=True) for d in self._devices.values()]

    async def heartbeat(self, device_id: str, status: DeviceStatus = DeviceStatus.ONLINE) -> bool:
        async with self._lock:
            if device_id in self._devices:
                dev = self._devices[device_id]
                dev.last_seen_at = datetime.now(timezone.utc)
                dev.status = status
                return True
            return False
