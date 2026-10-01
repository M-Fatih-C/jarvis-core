"""Configurable email synchronization scheduler with missed sync detection and explicit consent."""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta, timezone
from typing import Callable, Coroutine
from zoneinfo import ZoneInfo

from core.config.settings import Settings, get_settings
from core.logging.setup import get_logger
from integrations.gmail.pipeline import GmailPipelineResult, GmailProcessingPipeline

logger = get_logger("jarvis.gmail.scheduler")


class EmailSyncScheduler:
    """Schedules email synchronization at specified local times (default 09:00 & 20:00) with missed sync catch-up."""

    def __init__(
        self,
        pipeline: GmailProcessingPipeline,
        settings: Settings | None = None,
        sync_times: list[str] | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.settings = settings or get_settings()
        self.sync_times_str = sync_times or self.settings.email_sync_times
        # Background scheduling is strictly opt-in and disabled by default
        self.enabled = enabled if enabled is not None else self.settings.email_sync_schedule_enabled
        self.timezone_name = self.settings.default_timezone
        self._parsed_times: list[time] = self._parse_sync_times(self.sync_times_str)
        self.last_run: datetime | None = None
        self._running = False
        self._task: asyncio.Task[None] | None = None

    def _get_tz(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except Exception:
            return ZoneInfo("Europe/Istanbul")

    def _parse_sync_times(self, times: list[str]) -> list[time]:
        parsed = []
        for t_str in times:
            try:
                parts = t_str.strip().split(":")
                parsed.append(time(hour=int(parts[0]), minute=int(parts[1])))
            except Exception as exc:
                logger.warning("invalid_sync_time_format", time_str=t_str, error=str(exc))
        return sorted(parsed)

    def set_user_consent(self, consent: bool) -> None:
        """Explicitly enable or disable scheduled background email synchronization."""
        self.enabled = consent
        logger.info("email_sync_scheduler_consent_updated", enabled=self.enabled)
        if not consent and self._running:
            self.stop()

    def has_missed_sync(self, last_run: datetime | None, current_time: datetime) -> bool:
        """Check if any scheduled sync slot occurred between last_run and current_time."""
        if not self.enabled or not self._parsed_times:
            return False

        if last_run is None:
            # First execution, no missed check needed
            return False

        tz = self._get_tz()
        last_local = last_run.astimezone(tz)
        curr_local = current_time.astimezone(tz)

        # Check all days from last_local.date() to curr_local.date()
        cur_date = last_local.date()
        while cur_date <= curr_local.date():
            for t in self._parsed_times:
                slot = datetime.combine(cur_date, t, tzinfo=tz)
                if last_local < slot <= curr_local:
                    return True
            cur_date += timedelta(days=1)

        return False

    async def tick(self, now: datetime | None = None) -> GmailPipelineResult | None:
        """Evaluate schedule and run sync if a scheduled time arrived or was missed."""
        if not self.enabled:
            return None

        tz = self._get_tz()
        current_time = now or datetime.now(tz)

        should_run = False
        if self.last_run is None:
            # First tick after enabling
            should_run = True
        elif self.has_missed_sync(self.last_run, current_time):
            logger.info("missed_email_sync_detected_running_catchup")
            should_run = True

        if should_run:
            self.last_run = current_time
            return await self.pipeline.run()

        return None

    async def _loop(self, check_interval_seconds: float = 60.0) -> None:
        """Background monitoring loop."""
        while self._running:
            try:
                await self.tick()
            except Exception as exc:
                logger.error("scheduler_tick_error", error=str(exc))
            await asyncio.sleep(check_interval_seconds)

    def start(self, check_interval_seconds: float = 60.0) -> None:
        """Start scheduler background task if enabled."""
        if not self.enabled:
            logger.warning("cannot_start_scheduler_consent_not_given")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(check_interval_seconds))
        logger.info("email_sync_scheduler_started", times=self.sync_times_str, tz=self.timezone_name)

    def stop(self) -> None:
        """Stop scheduler background task."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        logger.info("email_sync_scheduler_stopped")
