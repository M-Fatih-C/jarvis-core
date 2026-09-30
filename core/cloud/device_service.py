"""Device registry service for fleet awareness and periodic health heartbeats."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from core.cloud.models import DeviceRecord, DeviceStatus
from core.cloud.repositories import DeviceRepository, InMemoryDeviceRepository
from core.config.settings import Settings, get_settings
from core.logging.setup import get_logger

logger = get_logger("jarvis.cloud.device")


class DeviceService:
    """Manages local device identity, registration, and periodic heartbeats."""

    def __init__(
        self,
        repository: DeviceRepository | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._repo = repository or InMemoryDeviceRepository()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def repository(self) -> DeviceRepository:
        return self._repo

    async def register_self(self) -> DeviceRecord:
        """Register or update current Mac device identity."""
        record = DeviceRecord(
            device_id=self._settings.device_id,
            type=self._settings.device_type,
            name=self._settings.device_name,
            status=DeviceStatus.ONLINE,
            app_version="0.1.0",
            capabilities=["local_llm", "memory", "tool_execution"],
            last_seen_at=datetime.now(timezone.utc),
        )
        saved = await self._repo.register_or_update(record)
        logger.info("device_registered", device_id=saved.device_id, status=saved.status.value)
        return saved

    async def beat(self, status: DeviceStatus = DeviceStatus.ONLINE) -> bool:
        """Issue an immediate heartbeat timestamp update."""
        success = await self._repo.heartbeat(self._settings.device_id, status=status)
        if success:
            logger.debug("device_heartbeat_sent", device_id=self._settings.device_id)
        return success

    async def _heartbeat_loop(self) -> None:
        interval = max(5, self._settings.device_heartbeat_interval_seconds)
        while self._running:
            try:
                await self.beat(status=DeviceStatus.ONLINE)
            except Exception as exc:
                logger.warning("device_heartbeat_failed", error=str(exc))
            await asyncio.sleep(interval)

    async def start(self) -> None:
        """Register device and start background heartbeat loop."""
        if self._running:
            return
        await self.register_self()
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("device_service_started")

    async def stop(self) -> None:
        """Stop heartbeat loop and gracefully mark device offline."""
        if not self._running:
            return
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        try:
            await self._repo.heartbeat(self._settings.device_id, status=DeviceStatus.OFFLINE)
        except Exception:
            pass
        logger.info("device_service_stopped")
