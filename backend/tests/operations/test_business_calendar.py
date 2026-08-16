"""Business-time arithmetic (SLA-01, contract C8).

The audit's failure scenario is the first test here and the reason for all the
others: eight-hour wait, two-hour reminders, cap of three, raised at 16:30 on a
Friday. Wall-clock arithmetic chased Support at 18:30, 20:30 and 22:30 into an
empty queue and parked the case at 00:30 on Saturday, having spent every
reminder it had while nobody was there.

`business_calendar_id` and `timezone` had been carried all the way onto
`ReturnCaseTimings` since the model was written; a repository-wide search for
either under `workflows/` returned only the two declaration lines.

Every calendar here is built from explicit working periods. There is no Mon-Fri
constant in the module under test, and the "the desk works Saturdays" and
"open around the clock" scenarios below are what proves it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.operations.business_calendar import (
    BusinessCalendar,
    WorkingPeriod,
    advance_business_time,
    is_working_time,
)
from return_platform.workflows.return_case_activities import ReturnCaseActivities
from return_platform.workflows.return_case_workflow import ResolveBusinessDeadlineInput

NEW_YORK = ZoneInfo("America/New_York")

#: 09:00-17:00, Monday to Friday, declared rather than assumed.
_NINE_TO_FIVE = tuple(
    WorkingPeriod(weekday=day, start_minute=9 * 60, end_minute=17 * 60) for day in range(5)
)


def _calendar(
    *,
    periods: tuple[WorkingPeriod, ...] = _NINE_TO_FIVE,
    timezone: str = "America/New_York",
    holidays: frozenset[date] = frozenset(),
) -> BusinessCalendar:
    return BusinessCalendar(
        calendar_id="test",
        timezone=timezone,
        working_periods=periods,
        holidays=holidays,
    )


def _local(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=NEW_YORK).astimezone(UTC)


def _in_new_york(instant: datetime) -> datetime:
    return instant.astimezone(NEW_YORK)


def test_the_friday_evening_case_the_audit_describes() -> None:
    """RG-06, as one assertion.

    A return raised at 16:30 on a Friday with an eight-working-hour wait must
    expire mid-morning on Monday, not late on Friday night.
    """
    calendar = _calendar()
    raised = _local(2026, 8, 14, 16, 30)  # a Friday

    deadline = _in_new_york(advance_business_time(calendar, raised, 8 * 3600))

    # Thirty minutes on Friday afternoon, then seven and a half hours from
    # Monday's 09:00 opening.
    assert deadline == datetime(2026, 8, 17, 16, 30, tzinfo=NEW_YORK)


def test_the_reminders_that_used_to_land_at_midnight_now_land_on_monday() -> None:
    """The other half of the scenario: the cadence, not just the deadline.

    Two-hour reminders from 16:30 on a Friday used to arrive at 18:30, 20:30
    and 22:30. All three now fall inside Monday's working day, which is the
    difference between three nudges a person sees and three a person does not.
    """
    calendar = _calendar()
    cursor = _local(2026, 8, 14, 16, 30)

    fired = []
    for _ in range(3):
        cursor = advance_business_time(calendar, cursor, 2 * 3600)
        fired.append(_in_new_york(cursor))

    assert fired == [
        datetime(2026, 8, 17, 10, 30, tzinfo=NEW_YORK),
        datetime(2026, 8, 17, 12, 30, tzinfo=NEW_YORK),
        datetime(2026, 8, 17, 14, 30, tzinfo=NEW_YORK),
    ]
    assert all(instant.weekday() == 0 for instant in fired), "no reminder fell on the weekend"


def test_a_weekend_is_skipped_entirely() -> None:
    calendar = _calendar()
    saturday = _local(2026, 8, 15, 11, 0)

    # Counting starts at the next opening, because the calendar is shut.
    deadline = _in_new_york(advance_business_time(calendar, saturday, 3600))

    assert deadline == datetime(2026, 8, 17, 10, 0, tzinfo=NEW_YORK)


def test_a_configured_holiday_is_skipped_like_a_weekend() -> None:
    """A holiday is a configured date, not a rule the code knows."""
    calendar = _calendar(holidays=frozenset({date(2026, 8, 17)}))
    friday = _local(2026, 8, 14, 16, 30)

    deadline = _in_new_york(advance_business_time(calendar, friday, 8 * 3600))

    # Monday is shut, so the seven and a half remaining hours come from Tuesday.
    assert deadline == datetime(2026, 8, 18, 16, 30, tzinfo=NEW_YORK)


def test_a_run_of_holidays_carries_across_days() -> None:
    calendar = _calendar(holidays=frozenset({date(2026, 11, 26), date(2026, 11, 27)}))
    wednesday = _local(2026, 11, 25, 16, 0)

    deadline = _in_new_york(advance_business_time(calendar, wednesday, 4 * 3600))

    # One hour on Wednesday; Thursday and Friday are shut; three hours Monday.
    assert deadline == datetime(2026, 11, 30, 12, 0, tzinfo=NEW_YORK)


def test_the_exact_closing_instant_belongs_to_the_day_that_is_closing() -> None:
    """A boundary that has to be decided the same way twice.

    17:00 is exclusive, so work counted right up to it finishes at 17:00 on the
    same day rather than at 09:00 the next -- and one further second lands the
    following morning.
    """
    calendar = _calendar()
    monday = _local(2026, 8, 17, 16, 0)

    assert _in_new_york(advance_business_time(calendar, monday, 3600)) == datetime(
        2026, 8, 17, 17, 0, tzinfo=NEW_YORK
    )
    assert _in_new_york(advance_business_time(calendar, monday, 3601)) == datetime(
        2026, 8, 18, 9, 0, 1, tzinfo=NEW_YORK
    )
    assert is_working_time(calendar, _local(2026, 8, 17, 16, 59))
    assert not is_working_time(calendar, _local(2026, 8, 17, 17, 0))


def test_a_business_day_boundary_starts_counting_at_the_opening() -> None:
    """Before opening is not working time, and does not consume the wait."""
    calendar = _calendar()
    early = _local(2026, 8, 17, 6, 0)

    assert not is_working_time(calendar, early)
    assert _in_new_york(advance_business_time(calendar, early, 1800)) == datetime(
        2026, 8, 17, 9, 30, tzinfo=NEW_YORK
    )


def test_a_short_reminder_interval_stays_inside_one_day() -> None:
    """The cadence must not round a fifteen-minute nudge to the next morning."""
    calendar = _calendar()
    cursor = _local(2026, 8, 17, 9, 0)

    for expected in (9, 9, 9, 10):
        cursor = advance_business_time(calendar, cursor, 900)
        assert _in_new_york(cursor).hour == expected


def test_spring_forward_does_not_lose_an_hour_of_the_wait() -> None:
    """DST, handled by construction rather than by arithmetic.

    2026-03-08 is the US spring-forward. The local working day is still eight
    hours long; it is the *UTC* span that changes, and a caller measuring in
    working seconds must not notice.
    """
    calendar = _calendar()
    friday = _local(2026, 3, 6, 16, 0)

    deadline = advance_business_time(calendar, friday, 9 * 3600)

    assert _in_new_york(deadline) == datetime(2026, 3, 9, 17, 0, tzinfo=NEW_YORK)
    # Monday is a 23-hour day in this zone; the working hours are unchanged.
    assert _in_new_york(deadline).utcoffset() == timedelta(hours=-4)


def test_fall_back_resolves_the_repeated_hour_to_the_earlier_instant() -> None:
    """2026-11-01 falls back. The conservative reading chases sooner."""
    calendar = _calendar(
        periods=tuple(
            WorkingPeriod(weekday=day, start_minute=0, end_minute=1440) for day in range(7)
        )
    )
    before = datetime(2026, 11, 1, 4, 0, tzinfo=UTC)

    # A continuous calendar is wall clock exactly -- that is the property, and
    # it is what a 24/7 operation configures.
    assert advance_business_time(calendar, before, 3600) == before + timedelta(hours=1)


def test_a_calendar_open_around_the_clock_is_wall_clock_exactly() -> None:
    """The migration path: declare every day whole, get the old behaviour."""
    calendar = _calendar(
        periods=tuple(
            WorkingPeriod(weekday=day, start_minute=0, end_minute=1440) for day in range(7)
        ),
        timezone="UTC",
    )
    friday = _local(2026, 8, 14, 16, 30)

    assert advance_business_time(calendar, friday, 8 * 3600) == friday + timedelta(hours=8)
    assert calendar.is_continuous


def test_a_saturday_desk_gets_saturdays() -> None:
    """No Mon-Fri anywhere: the code walks whatever is declared."""
    calendar = _calendar(
        periods=(*_NINE_TO_FIVE, WorkingPeriod(weekday=5, start_minute=9 * 60, end_minute=13 * 60))
    )
    friday = _local(2026, 8, 14, 16, 30)

    deadline = _in_new_york(advance_business_time(calendar, friday, 4 * 3600))

    # Half an hour Friday, then Saturday morning rather than Monday.
    assert deadline == datetime(2026, 8, 15, 12, 30, tzinfo=NEW_YORK)


def test_a_zero_duration_does_not_round_forward() -> None:
    """Zero working seconds from a Friday evening is that Friday evening.

    Rounding it to Monday would move a deadline nobody asked to move -- and
    `bay_wait_seconds` of 0 is a configured way to skip a wait entirely.
    """
    calendar = _calendar()
    friday_evening = _local(2026, 8, 14, 20, 0)

    assert advance_business_time(calendar, friday_evening, 0) == friday_evening


def test_a_naive_instant_is_refused() -> None:
    """Ambiguity about which zone an instant is in is never resolved silently."""
    with pytest.raises(ValueError, match="timezone-aware"):
        advance_business_time(_calendar(), datetime(2026, 8, 14, 16, 30), 3600)


def test_a_calendar_that_is_never_open_fails_loudly() -> None:
    """A deadline that can never arrive is a case nobody is ever told about."""
    calendar = BusinessCalendar(
        calendar_id="always-shut",
        timezone="UTC",
        working_periods=(WorkingPeriod(weekday=0, start_minute=0, end_minute=60),),
        # Every day shut for longer than the horizon the walk is bounded by.
        holidays=frozenset(date(2026, 1, 1) + timedelta(days=offset) for offset in range(1200)),
    )

    with pytest.raises(ValueError, match="no working time"):
        advance_business_time(calendar, datetime(2026, 8, 14, 16, tzinfo=UTC), 3600)


def test_an_unknown_timezone_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown business calendar timezone"):
        advance_business_time(
            _calendar(timezone="Mars/Olympus_Mons"),
            datetime(2026, 8, 14, 16, tzinfo=UTC),
            3600,
        )


# ---------------------------------------------------------------------------
# The activity the workflow calls, and the boundary it exists to keep
# ---------------------------------------------------------------------------


async def _resolve(
    configuration: Any, *, from_instant: datetime, seconds: int, calendar_id: str = "default"
) -> Any:
    activities = ReturnCaseActivities(
        repository=None,  # type: ignore[arg-type]
        support_service=None,
        configuration=lambda: configuration,
    )
    return await activities.resolve_business_deadline(
        ResolveBusinessDeadlineInput(
            from_iso=from_instant.isoformat(),
            working_seconds=seconds,
            business_calendar_id=calendar_id,
            timezone="UTC",
        )
    )


@pytest.mark.asyncio
async def test_the_activity_applies_the_configured_production_calendar() -> None:
    """The seam: configuration and the tz database are read here, not in the workflow.

    `SubmitOrderDiscoveryTurnCommand` already documents why a workflow body may
    not resolve a zone -- "a determinism hazard the moment the tz database on
    the worker changes" -- so the whole calculation lives in an activity whose
    single instant the workflow history records.
    """
    configuration = load_return_configuration(Path("config/returns/production.yaml")).configuration
    raised = _local(2026, 8, 14, 16, 30)

    resolved = await _resolve(configuration, from_instant=raised, seconds=8 * 3600)

    # Asserted against what the *shipped* calendar computes, not against a
    # hardcoded Monday. The seam this test exists for is "configuration and the
    # tz database are read in the activity"; pinning a specific instant here
    # additionally pins an operator's business decision, so this test failed the
    # day the deployment's calendar legitimately changed. The Mon-Fri
    # weekend-spanning arithmetic it used to demonstrate is covered directly,
    # and independently of any config file, by the tests above that build
    # `_NINE_TO_FIVE` themselves.
    shipped = next(c for c in configuration.business_calendars if c.calendar_id == "default")
    expected = advance_business_time(
        BusinessCalendar(
            calendar_id=shipped.calendar_id,
            timezone=shipped.timezone,
            working_periods=tuple(
                WorkingPeriod(
                    weekday=p.weekday, start_minute=p.start_minute, end_minute=p.end_minute
                )
                for p in shipped.working_periods
            ),
            holidays=frozenset(shipped.holidays),
        ),
        raised,
        8 * 3600,
    )

    assert resolved.calendar_applied is True
    assert datetime.fromisoformat(resolved.instant_iso) == expected


@pytest.mark.asyncio
async def test_the_shipped_calendar_and_the_support_sla_agree_about_which_clock_they_use() -> None:
    """The coupling behind D46, enforced rather than left in a comment.

    `item_reservation_ttl_seconds` is **wall clock**; `support_response_wait_
    seconds` is **business time**. While those disagreed -- 30 minutes against
    8 business hours -- there was a window between a hold lapsing and Support
    answering in which the units belonged to neither term of the availability
    formula, so a second case could be authorized for the same quantity and the
    total authorized could exceed the quantity ordered.

    Dev closes that by setting the SLA to the TTL **and** declaring the calendar
    24/7, which this file's own header names as the way to get wall-clock
    behaviour back exactly. Either change alone leaves the window open outside
    office hours.

    So the two must move together, and this test is what says so. Restoring the
    real Mon-Fri desk before live is expected to fail here -- that is the point:
    it is the reminder to restore `support_response_wait_seconds` too, and to
    close D46 properly with a fourth availability term rather than a 30-minute
    Support SLA nobody could honour.
    """
    configuration = load_return_configuration(Path("config/returns/production.yaml")).configuration
    shipped = next(c for c in configuration.business_calendars if c.calendar_id == "default")

    around_the_clock = len(shipped.working_periods) == 7 and all(
        period.start_minute == 0 and period.end_minute == 1440 for period in shipped.working_periods
    )
    sla_within_hold = (
        configuration.return_case.support_response_wait_seconds
        <= configuration.return_case.item_reservation_ttl_seconds
    )

    assert around_the_clock == sla_within_hold, (
        "the Support SLA and the calendar must be changed together: a business-time "
        "SLA longer than the wall-clock reservation TTL reopens the D46 overselling "
        "window, and a 24/7 calendar without a shortened SLA is merely a different desk"
    )


@pytest.mark.asyncio
async def test_an_undeclared_calendar_falls_back_to_wall_clock_and_says_so() -> None:
    """The behaviour before SLA-01, kept -- but no longer silent.

    A release that forgets its calendar must not stop cases from progressing;
    it must be visible. Inventing a default Mon-Fri instead would be wrong for
    most deployments and wrong invisibly.
    """
    configuration = load_return_configuration(Path("config/returns/production.yaml")).configuration
    raised = _local(2026, 8, 14, 16, 30)

    resolved = await _resolve(
        configuration, from_instant=raised, seconds=8 * 3600, calendar_id="not-declared"
    )

    assert resolved.calendar_applied is False
    assert datetime.fromisoformat(resolved.instant_iso) == raised + timedelta(hours=8)


@pytest.mark.asyncio
async def test_a_process_with_no_configuration_still_answers() -> None:
    """An activity that raised here would park every case over a config read."""
    raised = _local(2026, 8, 14, 16, 30)

    resolved = await _resolve(None, from_instant=raised, seconds=3600)

    assert resolved.calendar_applied is False
    assert datetime.fromisoformat(resolved.instant_iso) == raised + timedelta(hours=1)
