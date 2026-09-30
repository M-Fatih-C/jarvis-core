"""Deterministic temporal grounding utilities.

Ensures all relative dates, calendar proposals, and weekday/date pairs
are calculated with mathematical precision from Python datetime and zoneinfo,
preventing LLM hallucination of dates and weekdays.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import re
from typing import Any
from zoneinfo import ZoneInfo

from core.config.settings import get_settings

WEEKDAY_EN = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

WEEKDAY_TR = [
    "Pazartesi",
    "Salı",
    "Çarşamba",
    "Perşembe",
    "Cuma",
    "Cumartesi",
    "Pazar",
]

WEEKDAY_NAME_TO_INDEX: dict[str, int] = {
    # English
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
    # Turkish
    "pazartesi": 0,
    "ptesi": 0,
    "salı": 1,
    "sali": 1,
    "çarşamba": 2,
    "carsamba": 2,
    "perşembe": 3,
    "persembe": 3,
    "cuma": 4,
    "cumartesi": 5,
    "ctesi": 5,
    "pazar": 6,
}


def get_user_timezone(tz_name: str | None = None) -> ZoneInfo:
    """Return configured ZoneInfo or fallback to Europe/Istanbul or UTC."""
    name = tz_name or get_settings().default_timezone
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Istanbul")


def get_current_time_in_tz(tz_name: str | None = None) -> datetime:
    """Get current datetime localized to user timezone."""
    tz = get_user_timezone(tz_name)
    return datetime.now(tz=tz)


def weekday_for_date(d: date | datetime, locale: str = "en") -> str:
    """Return the deterministic weekday name for any date.
    
    Args:
        d: Date or datetime object.
        locale: 'en' for English ('Thursday'), 'tr' for Turkish ('Perşembe').
    """
    weekday_idx = d.weekday()  # Monday is 0, Sunday is 6
    if locale.lower() == "tr":
        return WEEKDAY_TR[weekday_idx]
    return WEEKDAY_EN[weekday_idx]


def resolve_relative_date(
    expression: str,
    anchor: datetime | None = None,
    tz_name: str | None = None,
) -> datetime:
    """Deterministically resolve relative temporal expressions against an anchor datetime.
    
    Supported expressions:
    - 'today', 'bugün' -> anchor date
    - 'tomorrow', 'yarın' -> anchor date + 1 day
    - 'yesterday', 'dün' -> anchor date - 1 day
    - 'day after tomorrow', 'öbür gün' -> anchor date + 2 days
    - 'next <weekday>' / 'gelecek <gün>' -> next occurrence of weekday
    """
    tz = get_user_timezone(tz_name)
    if anchor is None:
        anchor_dt = datetime.now(tz=tz)
    elif anchor.tzinfo is None:
        anchor_dt = anchor.replace(tzinfo=tz)
    else:
        anchor_dt = anchor.astimezone(tz)

    expr = expression.strip().lower()

    if expr in ("today", "bugün"):
        return anchor_dt
    if expr in ("tomorrow", "yarın"):
        return anchor_dt + timedelta(days=1)
    if expr in ("yesterday", "dün"):
        return anchor_dt - timedelta(days=1)
    if expr in ("day after tomorrow", "öbür gün", "yarından sonraki gün"):
        return anchor_dt + timedelta(days=2)

    # Next weekday matching (e.g. 'next monday', 'gelecek pazartesi')
    for name, target_idx in WEEKDAY_NAME_TO_INDEX.items():
        if f"next {name}" in expr or f"gelecek {name}" in expr or f"önümüzdeki {name}" in expr:
            days_ahead = (target_idx - anchor_dt.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return anchor_dt + timedelta(days=days_ahead)

    # Default fallback to tomorrow if ambiguous relative prompt
    if "yarın" in expr or "tomorrow" in expr:
        return anchor_dt + timedelta(days=1)

    return anchor_dt


def validate_temporal_reference(
    target_date: date | datetime | str,
    stated_weekday: str,
) -> bool:
    """Validate whether a stated weekday accurately corresponds to the target date.
    
    Args:
        target_date: Date object, datetime object, or 'YYYY-MM-DD' string.
        stated_weekday: Weekday string in English or Turkish (e.g. 'Thursday' or 'Perşembe').
        
    Returns:
        True if the weekday matches the date, False otherwise.
    """
    if isinstance(target_date, str):
        # Extract YYYY-MM-DD
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", target_date)
        if not m:
            raise ValueError(f"Invalid date format: {target_date}")
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    elif isinstance(target_date, datetime):
        d = target_date.date()
    else:
        d = target_date

    expected_idx = d.weekday()
    stated_clean = stated_weekday.strip().lower()
    actual_idx = WEEKDAY_NAME_TO_INDEX.get(stated_clean)
    return actual_idx == expected_idx


def get_deterministic_temporal_grounding(
    user_input: str,
    anchor: datetime | None = None,
    tz_name: str | None = None,
) -> str | None:
    """Analyze user prompt and produce explicit deterministic temporal grounding if relative dates are used."""
    tz = get_user_timezone(tz_name)
    if anchor is None:
        anchor_dt = datetime.now(tz=tz)
    elif anchor.tzinfo is None:
        anchor_dt = anchor.replace(tzinfo=tz)
    else:
        anchor_dt = anchor.astimezone(tz)

    input_lower = user_input.lower()
    has_tomorrow = bool(re.search(r"\b(yarın|tomorrow)\b", input_lower))
    has_today = bool(re.search(r"\b(bugün|today)\b", input_lower))

    if not (has_tomorrow or has_today):
        return None

    anchor_date_str = anchor_dt.strftime("%Y-%m-%d")
    anchor_weekday_en = weekday_for_date(anchor_dt, "en")
    anchor_weekday_tr = weekday_for_date(anchor_dt, "tr")

    tz_offset = anchor_dt.strftime("%z")
    formatted_offset = f"{tz_offset[:3]}:{tz_offset[3:]}" if len(tz_offset) == 5 else tz_offset

    grounding_lines = [
        "[Deterministic Temporal Grounding - System Source of Truth]",
        f"- Current Anchor Date: {anchor_date_str} ({anchor_weekday_en} / {anchor_weekday_tr})",
        f"- Timezone: {anchor_dt.tzinfo} ({formatted_offset})",
    ]

    if has_tomorrow:
        target_dt = anchor_dt + timedelta(days=1)
        target_date_str = target_dt.strftime("%Y-%m-%d")
        target_weekday_en = weekday_for_date(target_dt, "en")
        target_weekday_tr = weekday_for_date(target_dt, "tr")
        grounding_lines.extend([
            f"- Target Date for 'tomorrow' / 'yarın': {target_date_str}",
            f"- Target Weekday: {target_weekday_en} ({target_weekday_tr})",
            f"- HARD CONSTRAINT: Whenever referencing tomorrow or {target_date_str}, you MUST state that it is {target_weekday_en} ({target_weekday_tr}). NEVER hallucinate or state Monday, Tuesday, or any other weekday.",
        ])

    return "\n".join(grounding_lines)


def sanitize_response_temporal_consistency(
    response_text: str,
    anchor: datetime | None = None,
    tz_name: str | None = None,
) -> str:
    """Fail-closed deterministic post-processor that fixes any LLM date-weekday mismatch in response text.
    
    Example:
    'Tomorrow (Monday, 2026-10-01)' -> 'Tomorrow (Thursday, 2026-10-01)'
    'Yarın (Pazartesi, 2026-10-01)' -> 'Yarın (Perşembe, 2026-10-01)'
    """
    pattern = re.compile(
        r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
        r"Pazartesi|Salı|Çarşamba|Perşembe|Cuma|Cumartesi|Pazar)\b"
        r"([,\s]+)(\d{4}-\d{2}-\d{2})",
        re.IGNORECASE,
    )

    def _replace_weekday_date(match: re.Match[str]) -> str:
        stated_weekday = match.group(1)
        separator = match.group(2)
        date_str = match.group(3)
        try:
            d = date.fromisoformat(date_str)
            is_turkish = stated_weekday.lower() in [w.lower() for w in WEEKDAY_TR]
            correct_weekday = weekday_for_date(d, locale="tr" if is_turkish else "en")
            return f"{correct_weekday}{separator}{date_str}"
        except Exception:
            return match.group(0)

    # Reverse pattern: 2026-10-01 (Monday)
    pattern_rev = re.compile(
        r"(\d{4}-\d{2}-\d{2})([,\s\(\[]+)"
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
        r"Pazartesi|Salı|Çarşamba|Perşembe|Cuma|Cumartesi|Pazar)\b",
        re.IGNORECASE,
    )

    def _replace_date_weekday(match: re.Match[str]) -> str:
        date_str = match.group(1)
        separator = match.group(2)
        stated_weekday = match.group(3)
        try:
            d = date.fromisoformat(date_str)
            is_turkish = stated_weekday.lower() in [w.lower() for w in WEEKDAY_TR]
            correct_weekday = weekday_for_date(d, locale="tr" if is_turkish else "en")
            return f"{date_str}{separator}{correct_weekday}"
        except Exception:
            return match.group(0)

    text = pattern.sub(_replace_weekday_date, response_text)
    text = pattern_rev.sub(_replace_date_weekday, text)
    return text
