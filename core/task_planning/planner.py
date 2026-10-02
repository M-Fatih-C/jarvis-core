"""Intelligent Planning Engine: deterministic interval arithmetic, memory-informed slots, and deadline separation."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID

from core.agent.exceptions import JarvisError
from core.email_analysis.schemas import DeadlineConfidence, TaskProposal
from core.logging.setup import get_logger
from core.task_planning.schemas import (
    ActionDestination,
    ActionType,
    CalendarCategory,
    TaskActionProposal,
    TimeSlotProposal,
)
from core.task_planning.state import TaskProposalStatus
from integrations.macos.client import MacBridgeClient

logger = get_logger("jarvis.task_planning.planner")


class AmbiguousDeadlineError(JarvisError):
    """Raised when an action requires a definitive deadline but the task deadline is ambiguous or missing."""
    pass


class PlanningConflictError(JarvisError):
    """Raised when no conflict-free time slots could be found within the required window."""
    pass


class TaskPlanner:
    """Intelligent Planning Engine for Apple Calendar & Reminders."""

    def __init__(
        self,
        bridge_client: MacBridgeClient | None = None,
        buffer_minutes: int = 15,
        default_work_start_hour: int = 9,
        default_work_end_hour: int = 21,
    ) -> None:
        self.bridge_client = bridge_client or MacBridgeClient()
        self.buffer_minutes = buffer_minutes
        self.default_work_start_hour = default_work_start_hour
        self.default_work_end_hour = default_work_end_hour

    def plan_reminder_for_deadline(
        self,
        proposal: TaskProposal,
        custom_due_date: datetime | None = None,
        target_list_id: str | None = None,
    ) -> TaskActionProposal:
        """Create a Reminder action proposal for a deadline or follow-up item.

        Enforces strict temporal grounding: if the deadline is ambiguous,
        requires custom_due_date rather than hallucinating.

        Args:
            proposal: Source TaskProposal extracted from email.
            custom_due_date: Optional user-specified due date.
            target_list_id: Target Apple Reminders list ID.

        Returns:
            TaskActionProposal configured for Apple Reminders.

        Raises:
            AmbiguousDeadlineError: If deadline is uncertain and no custom_due_date provided.
        """
        due_at: datetime | None = None
        if custom_due_date is not None:
            due_at = custom_due_date
        elif proposal.deadline is not None and proposal.deadline_confidence in (
            DeadlineConfidence.EXACT,
            DeadlineConfidence.INFERRED,
        ):
            due_at = proposal.deadline
        else:
            raise AmbiguousDeadlineError(
                f"Görev '{proposal.title}' için son tarih belirsiz "
                f"(Güven derecesi: {proposal.deadline_confidence.value}, Metin: '{proposal.raw_deadline_text or 'yok'}'). "
                "Lütfen kesin bir son tarih ve saat seçin."
            )

        # Sanitize notes: do NOT copy full raw body or tokens into notes
        notes = f"Jarvis Görev Önerisi (Öncelik: {proposal.priority.value})\nKaynak: {proposal.proposed_action}"

        return TaskActionProposal(
            task_id=proposal.task_id,
            source_message_id=proposal.source_message_id,
            action_type=ActionType.REMINDER,
            target_destination=ActionDestination.APPLE_REMINDERS,
            title=proposal.title,
            notes=notes,
            due_date=due_at,
            target_list_id=target_list_id,
            logical_category=CalendarCategory.WORK,
            status=TaskProposalStatus.PROPOSED,
        )

    def plan_calendar_milestone_event(
        self,
        proposal: TaskProposal,
        start_time: datetime,
        end_time: datetime,
        target_calendar_id: str | None = None,
        logical_category: CalendarCategory = CalendarCategory.WORK,
    ) -> TaskActionProposal:
        """Plan a fixed milestone or appointment event (e.g. Exam date/time, distinct from prep blocks)."""
        if end_time <= start_time:
            raise JarvisError("Bitiş zamanı başlangıç zamanından sonra olmalıdır.")

        notes = f"Etkinlik / Milestone: {proposal.title}\nÖncelik: {proposal.priority.value}"

        return TaskActionProposal(
            task_id=proposal.task_id,
            source_message_id=proposal.source_message_id,
            action_type=ActionType.CALENDAR_EVENT,
            target_destination=ActionDestination.APPLE_CALENDAR,
            title=proposal.title,
            notes=notes,
            start_time=start_time,
            end_time=end_time,
            target_calendar_id=target_calendar_id,
            logical_category=logical_category,
            status=TaskProposalStatus.PROPOSED,
        )

    async def plan_work_block(
        self,
        proposal: TaskProposal,
        duration_minutes: int = 120,
        target_date_range: tuple[datetime, datetime] | None = None,
        target_calendar_id: str | None = None,
        logical_category: CalendarCategory = CalendarCategory.WORK,
        memory_service: Any = None,
        selected_slot_index: int = 0,
    ) -> tuple[TaskActionProposal, list[TimeSlotProposal]]:
        """Plan a focused study or work block for a task using deterministic availability calculation.

        Separates deadline from prep session.
        Informs slot selection using memory preferences (e.g. evening study preference).

        Args:
            proposal: The TaskProposal to prepare for.
            duration_minutes: Estimated duration in minutes (default 2 hours).
            target_date_range: Optional (start, end) bounding window to search for slots.
            target_calendar_id: Target Apple Calendar identifier.
            logical_category: Logical category (WORK, EDUCATION, PERSONAL, PROJECT).
            memory_service: Optional MemoryService to query work/study habits.
            selected_slot_index: Index of the proposed slot to select as primary.

        Returns:
            Tuple of (primary_action_proposal, list_of_all_candidate_slots).
        """
        tz = timezone.utc
        now = datetime.now(tz)

        # 1. Determine bounding date window
        if target_date_range is not None:
            range_start, range_end = target_date_range
            tz = range_start.tzinfo or timezone.utc
        else:
            # Default: from tomorrow 09:00 until either the deadline or 5 days out
            tomorrow = (now + timedelta(days=1)).replace(
                hour=self.default_work_start_hour, minute=0, second=0, microsecond=0
            )
            range_start = tomorrow
            if proposal.deadline and proposal.deadline > tomorrow:
                # Work block must occur before the deadline
                range_end = proposal.deadline
            else:
                range_end = range_start + timedelta(days=5)

        if range_end <= range_start:
            range_end = range_start + timedelta(days=1)

        # 2. Query user memory for study/work preferences
        preferred_hours: tuple[int, int] | None = None
        preference_reason: str = "Standard working hours"
        if memory_service is not None:
            try:
                mem_results = await memory_service.search(
                    query=f"çalışma saatleri ve ders çalışma tercihi {proposal.category.value}",
                    top_k=3,
                )
                for item in mem_results:
                    content_lower = item.content.lower()
                    if "akşam" in content_lower or "evening" in content_lower or "19:00" in content_lower or "20:00" in content_lower:
                        preferred_hours = (19, 22)
                        preference_reason = "Kullanıcı hafıza tercihi: Akşam çalışma bloğu (19:00–22:00)"
                        break
                    elif "sabah" in content_lower or "morning" in content_lower:
                        preferred_hours = (9, 12)
                        preference_reason = "Kullanıcı hafıza tercihi: Sabah çalışma bloğu (09:00–12:00)"
                        break
            except Exception as mem_err:
                logger.warning("memory_preference_lookup_failed", error=str(mem_err))

        # 3. Read existing Apple Calendar events in target window via bridge
        existing_events: list[dict[str, Any]] = []
        try:
            res = await self.bridge_client.call(
                "calendar.list_events",
                {
                    "start": range_start.isoformat(),
                    "end": range_end.isoformat(),
                    "limit": 100,
                },
            )
            existing_events = res.get("events", [])
        except Exception as bridge_err:
            logger.warning("calendar_list_events_failed_using_empty", error=str(bridge_err))

        # 4. Deterministic interval arithmetic to compute free slots
        candidate_slots = self.calculate_conflict_free_slots(
            search_start=range_start,
            search_end=range_end,
            duration_minutes=duration_minutes,
            existing_events=existing_events,
            daily_start_hour=self.default_work_start_hour,
            daily_end_hour=self.default_work_end_hour,
            preferred_hours=preferred_hours,
            preferred_reason=preference_reason,
            buffer_minutes=self.buffer_minutes,
        )

        if not candidate_slots:
            raise PlanningConflictError(
                f"Belirtilen aralıkta ({range_start.strftime('%Y-%m-%d')} - {range_end.strftime('%Y-%m-%d')}) "
                f"{duration_minutes} dakikalık çakışmasız çalışma aralığı bulunamadı."
            )

        # 5. Select primary slot
        slot_idx = min(selected_slot_index, len(candidate_slots) - 1)
        chosen_slot = candidate_slots[slot_idx]

        action_title = f"Çalışma Bloğu: {proposal.title}"
        notes = (
            f"Hazırlık Bloğu: {proposal.title}\n"
            f"Süre: {duration_minutes} dk\n"
            f"Planlama Kriteri: {chosen_slot.reason}"
        )

        proposal_action = TaskActionProposal(
            task_id=proposal.task_id,
            source_message_id=proposal.source_message_id,
            action_type=ActionType.WORK_BLOCK,
            target_destination=ActionDestination.APPLE_CALENDAR,
            title=action_title,
            notes=notes,
            start_time=chosen_slot.start,
            end_time=chosen_slot.end,
            target_calendar_id=target_calendar_id,
            logical_category=logical_category,
            status=TaskProposalStatus.PROPOSED,
        )

        return proposal_action, candidate_slots

    def calculate_conflict_free_slots(
        self,
        search_start: datetime,
        search_end: datetime,
        duration_minutes: int,
        existing_events: list[dict[str, Any]],
        daily_start_hour: int = 9,
        daily_end_hour: int = 21,
        preferred_hours: tuple[int, int] | None = None,
        preferred_reason: str = "Standard window",
        buffer_minutes: int = 15,
    ) -> list[TimeSlotProposal]:
        """Compute conflict-free time slots using pure deterministic interval arithmetic.

        Zero LLM hallucinations:
        1. Bounded daily windows [daily_start_hour, daily_end_hour].
        2. Events expanded with buffer_minutes before & after.
        3. Busy intervals merged.
        4. Free intervals calculated and partitioned into slots >= duration_minutes.
        5. Ranked by user preference score and chronological proximity.
        """
        tz = search_start.tzinfo or timezone.utc
        slots: list[TimeSlotProposal] = []
        req_delta = timedelta(minutes=duration_minutes)
        buf_delta = timedelta(minutes=buffer_minutes)

        # Parse and expand existing busy intervals
        busy_intervals: list[tuple[datetime, datetime]] = []
        for ev in existing_events:
            start_str = ev.get("start")
            end_str = ev.get("end")
            if not start_str or not end_str:
                continue
            try:
                ev_start = datetime.fromisoformat(start_str)
                ev_end = datetime.fromisoformat(end_str)
                if ev_start.tzinfo is None:
                    ev_start = ev_start.replace(tzinfo=tz)
                if ev_end.tzinfo is None:
                    ev_end = ev_end.replace(tzinfo=tz)
                # Apply safety buffer
                busy_intervals.append((ev_start - buf_delta, ev_end + buf_delta))
            except Exception:
                continue

        # Sort and merge overlapping busy intervals
        busy_intervals.sort(key=lambda x: x[0])
        merged_busy: list[tuple[datetime, datetime]] = []
        for interval in busy_intervals:
            if not merged_busy:
                merged_busy.append(interval)
            else:
                last_start, last_end = merged_busy[-1]
                if interval[0] <= last_end:
                    merged_busy[-1] = (last_start, max(last_end, interval[1]))
                else:
                    merged_busy.append(interval)

        # Iterate day by day across search range
        current_day = search_start.date()
        end_day = search_end.date()

        while current_day <= end_day:
            day_win_start = datetime.combine(
                current_day, time(hour=daily_start_hour, minute=0), tzinfo=tz
            )
            day_win_end = datetime.combine(
                current_day, time(hour=daily_end_hour, minute=0), tzinfo=tz
            )

            # Clamp window to search_start and search_end
            win_start = max(day_win_start, search_start)
            win_end = min(day_win_end, search_end)

            if win_end > win_start and (win_end - win_start) >= req_delta:
                # Find available free segments within [win_start, win_end]
                cursor = win_start
                # Filter busy intervals intersecting this window
                day_busy = [
                    (max(b[0], win_start), min(b[1], win_end))
                    for b in merged_busy
                    if b[1] > win_start and b[0] < win_end
                ]
                day_busy.sort(key=lambda x: x[0])

                for b_start, b_end in day_busy:
                    if b_start > cursor:
                        free_seg_start = cursor
                        free_seg_end = b_start
                        while (free_seg_end - free_seg_start) >= req_delta:
                            slot_end = free_seg_start + req_delta
                            slots.append(
                                self._build_slot(
                                    free_seg_start,
                                    slot_end,
                                    duration_minutes,
                                    preferred_hours,
                                    preferred_reason,
                                )
                            )
                            # Advance by step (e.g. 60 mins or full duration)
                            free_seg_start += timedelta(minutes=60)
                    cursor = max(cursor, b_end)

                if cursor < win_end:
                    free_seg_start = cursor
                    free_seg_end = win_end
                    while (free_seg_end - free_seg_start) >= req_delta:
                        slot_end = free_seg_start + req_delta
                        slots.append(
                            self._build_slot(
                                free_seg_start,
                                slot_end,
                                duration_minutes,
                                preferred_hours,
                                preferred_reason,
                            )
                        )
                        free_seg_start += timedelta(minutes=60)

            current_day += timedelta(days=1)

        # Sort slots: highest score first, then earliest start time
        slots.sort(key=lambda s: (-s.score, s.start))
        return slots

    def _build_slot(
        self,
        start: datetime,
        end: datetime,
        duration_minutes: int,
        preferred_hours: tuple[int, int] | None,
        preferred_reason: str,
    ) -> TimeSlotProposal:
        """Score candidate slot based on preference matching."""
        score = 1.0
        reason = "Çakışmasız uygun takvim aralığı"

        if preferred_hours is not None:
            pref_start, pref_end = preferred_hours
            if start.hour >= pref_start and end.hour <= pref_end:
                score += 2.0
                reason = preferred_reason
            elif start.hour >= pref_start or end.hour <= pref_end:
                score += 1.0
                reason = f"Kısmen uyan aralık ({preferred_reason})"

        return TimeSlotProposal(
            start=start,
            end=end,
            duration_minutes=duration_minutes,
            score=score,
            reason=reason,
        )
