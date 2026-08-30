"""The acceptance calendar actually shuts (ACC brief, item 2).

A fixture that is only ever consumed is a fixture nobody has checked. These are
the three gaps every scenario in items 13/19 depends on -- overnight, weekend,
and a deadline whose remainder has to be paid out on the far side of a weekend
-- asserted directly against the pure functions in
`operations/business_calendar.py`, with no workflow and no datastore anywhere
near them.

The dates are a real week: 2026-08-14 is a Friday, 2026-08-15/16 the weekend,
2026-08-17 the Monday. Written as local wall-clock instants in the desk's own
zone, because that is the frame the calendar is declared in and converting by
hand is how an off-by-one-hour assertion gets written and then believed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.operations.business_calendar import (
    BusinessCalendar,
    advance_business_time,
    is_working_time,
)
from return_platform.workflows.return_case_activities import ReturnCaseActivities
from return_platform.workflows.return_case_workflow import ResolveBusinessDeadlineInput
from tests.harness.business_calendars import (
    DESK_TIMEZONE,
    as_business_calendar,
    nine_to_five_configuration,
    with_business_calendar,
)

DESK = ZoneInfo(DESK_TIMEZONE)

FRIDAY = date(2026, 8, 14)
SATURDAY = date(2026, 8, 15)
MONDAY = date(2026, 8, 17)
TUESDAY = date(2026, 8, 18)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    """A local wall-clock time at the desk, as an absolute instant."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=DESK).astimezone(UTC)


def _local(instant: datetime) -> datetime:
    return instant.astimezone(DESK)


def test_the_week_these_tests_assume_is_the_week_the_calendar_sees() -> None:
    """The dates are load-bearing, so they are asserted rather than trusted.

    Every expectation below is "…because that day is a Saturday". A typo in a
    date would turn each of them into an assertion about a different day of the
    week, still passing or failing for reasons no message would explain.
    """
    assert (FRIDAY.weekday(), SATURDAY.weekday(), MONDAY.weekday(), TUESDAY.weekday()) == (
        4,
        5,
        0,
        1,
    )


def test_the_fixture_declares_a_desk_that_closes(
    business_hours_calendar: BusinessCalendar,
) -> None:
    """The premise, in one assertion.

    `production.yaml` ships `business_calendars.default` as 24/7 -- and a
    continuous calendar is wall clock *exactly*, by `advance_business_time`'s
    own short-circuit. Against it, every gap below is zero seconds wide and
    every scenario about weekends silently tests addition. `is_continuous`
    being false is what makes the rest of this file mean anything.
    """
    assert not business_hours_calendar.is_continuous
    assert business_hours_calendar.timezone == DESK_TIMEZONE


def test_an_overnight_gap_is_not_counted(business_hours_calendar: BusinessCalendar) -> None:
    """Two working hours from 16:00 land at 10:00, not at 18:00.

    One hour before Monday's close, one after Tuesday's opening; the sixteen
    hours the desk is shut cost nothing. This is the difference between a
    reminder a person sees and a reminder fired into an empty queue.
    """
    deadline = advance_business_time(business_hours_calendar, _at(MONDAY, 16), 2 * 3600)

    assert _local(deadline) == datetime(2026, 8, 18, 10, 0, tzinfo=DESK)


def test_a_weekend_gap_is_not_counted(business_hours_calendar: BusinessCalendar) -> None:
    """The same two hours from Friday 16:00 cross a weekend and still cost two hours."""
    deadline = advance_business_time(business_hours_calendar, _at(FRIDAY, 16), 2 * 3600)

    assert _local(deadline) == datetime(2026, 8, 17, 10, 0, tzinfo=DESK)
    assert _local(deadline).weekday() == 0, "a deadline was allowed to land on the weekend"


def test_a_deadline_lands_after_the_weekend_with_its_remainder_intact(
    business_hours_calendar: BusinessCalendar,
) -> None:
    """The audit's scenario: raised 16:30 Friday, eight working hours.

    Thirty minutes are paid out of Friday afternoon and the remaining seven and
    a half from Monday's opening -- so the deadline is 16:30 on Monday, not
    00:30 on Saturday. The remainder surviving the gap is the property; a
    calendar that restarted the count on Monday morning would also produce a
    Monday, and would be wrong.
    """
    deadline = advance_business_time(business_hours_calendar, _at(FRIDAY, 16, 30), 8 * 3600)

    assert _local(deadline) == datetime(2026, 8, 17, 16, 30, tzinfo=DESK)


def test_a_weekend_start_waits_for_the_opening_rather_than_counting_from_itself(
    business_hours_calendar: BusinessCalendar,
) -> None:
    """A restart on a Saturday must not fire a retroactive burst on resume (item 19).

    Counting begins at the next opening when the desk is shut, so an hour of
    business time from Saturday lunchtime is Monday at 10:00 -- one reminder,
    on Monday, rather than a weekend's worth arriving at once.
    """
    deadline = advance_business_time(business_hours_calendar, _at(SATURDAY, 11), 3600)

    assert _local(deadline) == datetime(2026, 8, 17, 10, 0, tzinfo=DESK)


def test_is_working_time_agrees_with_the_gaps(business_hours_calendar: BusinessCalendar) -> None:
    """The predicate and the arithmetic must describe the same desk.

    17:00 is exclusive and 09:00 inclusive -- the boundary is asserted in both
    directions because a scenario that polls "is the desk open?" and a scenario
    that computes a deadline must never disagree about the same instant.
    """
    assert is_working_time(business_hours_calendar, _at(MONDAY, 9))
    assert is_working_time(business_hours_calendar, _at(MONDAY, 16, 59))
    assert not is_working_time(business_hours_calendar, _at(MONDAY, 8, 59))
    assert not is_working_time(business_hours_calendar, _at(MONDAY, 17))
    assert not is_working_time(business_hours_calendar, _at(FRIDAY, 21)), "overnight"
    assert not is_working_time(business_hours_calendar, _at(SATURDAY, 11)), "weekend"


def test_a_declared_holiday_is_skipped_like_a_weekend() -> None:
    """The factory takes holidays, and they behave as whole shut days.

    Exercised because item 13's cadence scenarios are the natural place for a
    holiday to appear, and a keyword argument nothing has ever passed is a
    keyword argument nobody has checked.
    """
    calendar = as_business_calendar(nine_to_five_configuration(holidays=(MONDAY,)))

    deadline = advance_business_time(calendar, _at(FRIDAY, 16, 30), 8 * 3600)

    assert _local(deadline) == datetime(2026, 8, 18, 16, 30, tzinfo=DESK)


@pytest.mark.asyncio
async def test_the_fixture_maps_to_the_calendar_production_would_build() -> None:
    """The duplication this fixture carries, held to account.

    `as_business_calendar` re-derives a mapping that already exists inside
    `ReturnCaseActivities._business_calendar`, which is private. Rather than
    reach into it, the real activity is run over a configuration carrying this
    calendar and the two answers are compared. A change to the production
    mapping -- a different timezone precedence, a dropped holiday set -- fails
    here as a disagreement about one instant, instead of leaving every
    acceptance scenario asserting against a desk the platform does not have.

    **The calendar declares a holiday, and it has to.** This pin ran against a
    calendar with none, so the holiday half of the claim above was decoration:
    an empty set maps to an empty set whether the mapping copies it or drops it
    on the floor, and replacing `frozenset(declared.holidays)` with
    `frozenset()` in production left this file green. Every field the mapping
    carries has to be non-default here or it is not being compared at all --
    which is why the probe below starts on Friday and crosses `MONDAY`, so a
    dropped holiday moves the answer by a whole day.
    """
    declared = nine_to_five_configuration(holidays=(MONDAY,))
    configuration = with_business_calendar(
        load_return_configuration(Path("config/returns/production.yaml")).configuration,
        declared,
    )
    raised = _at(FRIDAY, 16, 30)

    activities = ReturnCaseActivities(
        repository=None,  # type: ignore[arg-type]
        support_service=None,
        configuration=lambda: configuration,
    )
    resolved = await activities.resolve_business_deadline(
        ResolveBusinessDeadlineInput(
            from_iso=raised.isoformat(),
            working_seconds=8 * 3600,
            business_calendar_id=declared.calendar_id,
            timezone="UTC",
        )
    )

    assert resolved.calendar_applied is True, (
        "the activity did not find the installed calendar -- `with_business_calendar` "
        "no longer points `return_case.business_calendar_id` at it, or the id collides"
    )
    assert datetime.fromisoformat(resolved.instant_iso) == advance_business_time(
        as_business_calendar(declared), raised, 8 * 3600
    )


def test_installing_the_calendar_replaces_rather_than_shadows_a_same_id_entry() -> None:
    """`_business_calendar` returns the first match, so a duplicate id never wins.

    The shipped `default` is the id a scenario is most likely to reuse, and an
    appended second `default` would be a calendar that is installed, named, and
    never consulted -- passing tests, wall-clock behaviour.
    """
    configuration = load_return_configuration(Path("config/returns/production.yaml")).configuration
    shipped_ids = [entry.calendar_id for entry in configuration.business_calendars]
    assert "default" in shipped_ids, "the shipped configuration no longer declares `default`"

    updated = with_business_calendar(
        configuration, nine_to_five_configuration(calendar_id="default")
    )

    installed = [entry for entry in updated.business_calendars if entry.calendar_id == "default"]
    assert len(installed) == 1
    assert not as_business_calendar(installed[0]).is_continuous
    assert updated.return_case.business_calendar_id == "default"
