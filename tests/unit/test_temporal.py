"""Unit and regression tests for deterministic temporal grounding and weekday validation."""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import pytest

from core.time.temporal import (
    get_current_time_in_tz,
    get_deterministic_temporal_grounding,
    get_user_timezone,
    resolve_relative_date,
    sanitize_response_temporal_consistency,
    validate_temporal_reference,
    weekday_for_date,
)


def test_weekday_for_date_regression() -> None:
    # Anchor date: 2026-09-30 is Wednesday
    anchor = date(2026, 9, 30)
    assert weekday_for_date(anchor, "en") == "Wednesday"
    assert weekday_for_date(anchor, "tr") == "Çarşamba"

    # Target date: 2026-10-01 MUST be Thursday (NOT Monday)
    target = date(2026, 10, 1)
    assert weekday_for_date(target, "en") == "Thursday"
    assert weekday_for_date(target, "tr") == "Perşembe"


def test_resolve_relative_date_regression() -> None:
    tz = ZoneInfo("Europe/Istanbul")
    anchor_dt = datetime(2026, 9, 30, 21, 0, 0, tzinfo=tz)

    # 2026-09-30 + tomorrow -> 2026-10-01
    tomorrow_en = resolve_relative_date("tomorrow", anchor=anchor_dt, tz_name="Europe/Istanbul")
    assert tomorrow_en.date() == date(2026, 10, 1)
    assert weekday_for_date(tomorrow_en, "en") == "Thursday"

    tomorrow_tr = resolve_relative_date("yarın", anchor=anchor_dt, tz_name="Europe/Istanbul")
    assert tomorrow_tr.date() == date(2026, 10, 1)
    assert weekday_for_date(tomorrow_tr, "tr") == "Perşembe"


def test_europe_istanbul_offset_regression() -> None:
    tz = ZoneInfo("Europe/Istanbul")
    dt = datetime(2026, 10, 1, 14, 0, 0, tzinfo=tz)
    offset = dt.strftime("%z")
    assert offset in ("+0300", "+03:00")
    formatted_offset = f"{offset[:3]}:{offset[3:]}" if len(offset) == 5 else offset
    assert formatted_offset == "+03:00"


def test_validate_temporal_reference() -> None:
    target = date(2026, 10, 1)
    # Valid
    assert validate_temporal_reference(target, "Thursday") is True
    assert validate_temporal_reference(target, "Perşembe") is True
    assert validate_temporal_reference("2026-10-01", "Thursday") is True
    assert validate_temporal_reference("2026-10-01", "Perşembe") is True

    # Invalid (hallucinated Monday)
    assert validate_temporal_reference(target, "Monday") is False
    assert validate_temporal_reference(target, "Pazartesi") is False


def test_sanitize_response_temporal_consistency() -> None:
    anchor = datetime(2026, 9, 30, 12, 0, 0, tzinfo=ZoneInfo("Europe/Istanbul"))

    # Hallucinated Monday with 2026-10-01
    bad_resp_en = "Tomorrow (Monday, 2026-10-01) is a good time."
    fixed_en = sanitize_response_temporal_consistency(bad_resp_en, anchor=anchor)
    assert "Tomorrow (Thursday, 2026-10-01)" in fixed_en
    assert "Monday" not in fixed_en

    # Hallucinated Turkish Pazartesi with 2026-10-01
    bad_resp_tr = "Yarın (Pazartesi, 2026-10-01) için hatırlatıcı oluşturuyorum."
    fixed_tr = sanitize_response_temporal_consistency(bad_resp_tr, anchor=anchor)
    assert "Yarın (Perşembe, 2026-10-01)" in fixed_tr
    assert "Pazartesi" not in fixed_tr

    # Reverse format: 2026-10-01 (Monday)
    bad_resp_rev = "Date: 2026-10-01 (Monday)"
    fixed_rev = sanitize_response_temporal_consistency(bad_resp_rev, anchor=anchor)
    assert "Date: 2026-10-01 (Thursday)" in fixed_rev


def test_deterministic_temporal_grounding_generation() -> None:
    tz = ZoneInfo("Europe/Istanbul")
    anchor_dt = datetime(2026, 9, 30, 20, 0, 0, tzinfo=tz)

    user_query = "Yarın Jarvis projesine iki saat ayırmak istiyorum."
    grounding = get_deterministic_temporal_grounding(user_query, anchor=anchor_dt, tz_name="Europe/Istanbul")
    assert grounding is not None
    assert "2026-10-01" in grounding
    assert "Thursday" in grounding
    assert "Perşembe" in grounding


def test_calendar_week_is_monday_bounded_across_months():
    from zoneinfo import ZoneInfo
    from core.time.temporal import get_deterministic_temporal_grounding
    anchor = datetime(2026, 10, 3, 17, 0, tzinfo=ZoneInfo('Europe/Istanbul'))
    current = get_deterministic_temporal_grounding('Bu haftaki programım?', anchor=anchor)
    assert '2026-09-28T00:00:00+03:00' in current
    assert '2026-10-05T00:00:00+03:00' in current
    upcoming = get_deterministic_temporal_grounding('Gelecek hafta', anchor=anchor)
    assert '2026-10-05T00:00:00+03:00' in upcoming
    assert '2026-10-12T00:00:00+03:00' in upcoming
