"""Apple Calendar Target Manager for EventKit discovery, iCloud preference, and logical categories."""

from __future__ import annotations

from typing import Any

from core.config.settings import Settings, get_settings
from core.logging.setup import get_logger
from core.task_planning.schemas import CalendarCategory, CalendarTargetInfo
from integrations.macos.client import MacBridgeClient

logger = get_logger("jarvis.task_planning.calendar_manager")


class CalendarTargetManager:
    """Manages writable Apple Calendar discovery, preference for iCloud, and logical categories."""

    def __init__(
        self,
        bridge_client: MacBridgeClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.bridge_client = bridge_client or MacBridgeClient()
        self.settings = settings or get_settings()
        self._selected_calendar_id: str | None = getattr(self.settings, "selected_calendar_id", None)

    async def list_writable_calendars(self) -> list[CalendarTargetInfo]:
        """Fetch all writable calendars from Apple Calendar via EventKit bridge."""
        try:
            res = await self.bridge_client.call("calendar.list_calendars", {})
            raw_cals = res.get("calendars", [])
        except Exception as exc:
            logger.warning("failed_to_list_calendars_from_bridge", error=str(exc))
            raw_cals = []

        writable_cals: list[CalendarTargetInfo] = []
        for c in raw_cals:
            is_writable = bool(c.get("allows_modifications", False))
            if not is_writable:
                continue

            title = str(c.get("title", ""))
            source_title = c.get("source_title") or ""
            source_type = c.get("source_type") or ""

            # Detect iCloud vs local 'On My Mac'
            is_icloud = (
                source_title.strip().lower() == "icloud"
                or "icloud" in title.lower()
                or source_type.lower() == "caldav"
            )
            is_on_my_mac = (
                source_title.strip().lower() in ("on my mac", "local", "default")
                or "on my mac" in title.lower()
                or source_type.lower() == "local"
            )

            writable_cals.append(
                CalendarTargetInfo(
                    id=str(c.get("id", "")),
                    title=title,
                    is_writable=is_writable,
                    is_icloud=is_icloud,
                    is_on_my_mac=is_on_my_mac,
                    source_title=source_title if source_title else None,
                    source_type=source_type if source_type else None,
                )
            )

        return writable_cals

    async def get_preferred_calendar(self) -> tuple[CalendarTargetInfo | None, str | None]:
        """Determine target calendar.

        Prefers configured selection, then iCloud calendars.
        Strictly avoids writing to 'On My Mac' without explicit selection.

        Returns:
            (selected_calendar, warning_message)
        """
        cals = await self.list_writable_calendars()
        if not cals:
            return None, "No writable Apple Calendars found on system."

        # 1. Check explicit selection
        if self._selected_calendar_id:
            for c in cals:
                if c.id == self._selected_calendar_id:
                    return c, None

        # 2. Check for iCloud calendar
        icloud_cals = [c for c in cals if c.is_icloud]
        if icloud_cals:
            # Prefer 'Calendar' or 'Personal' or first iCloud calendar
            chosen = icloud_cals[0]
            for c in icloud_cals:
                if c.title.lower() in ("calendar", "genel", "takvim", "work", "kişisel"):
                    chosen = c
                    break
            return chosen, None

        # 3. iCloud not found: notify user and avoid accidental write to 'On My Mac'
        local_cals = [c for c in cals if c.is_on_my_mac]
        if local_cals:
            warning = (
                "No iCloud calendar found. Available calendars are stored locally on 'On My Mac'. "
                "Jarvis will not write to local calendars without explicit selection."
            )
            logger.warning("calendar_no_icloud_warning", warning=warning)
            return None, warning

        # 4. Other calendar (e.g. Exchange, Google CalDAV)
        return cals[0], None

    def set_selected_calendar_id(self, calendar_id: str) -> None:
        """Store user selected calendar ID."""
        self._selected_calendar_id = calendar_id

    def validate_category(self, category: str | CalendarCategory) -> CalendarCategory:
        """Map and validate logical categories: WORK, EDUCATION, PERSONAL, PROJECT."""
        if isinstance(category, CalendarCategory):
            return category
        raw = category.strip().upper()
        try:
            return CalendarCategory(raw)
        except ValueError:
            return CalendarCategory.WORK
