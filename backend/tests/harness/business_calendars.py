"""A desk that closes at night and does not work weekends (ACC brief, item 2).

`config/returns/production.yaml` ships `business_calendars.default` as a
**24/7** dev calendar -- every day 0..1440 -- on purpose, and its own comment
names the consequence: "every deadline and reminder computed on this calendar
becomes wall clock too. In dev that is the point." Which means the shipped
configuration cannot exercise a single business-time behaviour. A weekend that
is never skipped is a weekend the reminder cadence is never tested against, and
scenarios 13/19 -- per-case cadence capped at three across N reviews, no
duplicate reminders across a clarification round-trip, a weekend-spanning
restart firing no retroactive burst -- are all statements about a calendar that
shuts.

So the calendar under acceptance is declared here, in the tests, rather than by
editing what the deployment ships. Mon-Fri 09:00-17:00, `America/New_York` --
the desk's real zone, DST and all, because a fixture pinned to UTC would quietly
stop testing the one construction (`fold`, local wall-clock days) that
`business_calendar.py` spends a third of its docstring on.

**No Mon-Fri constant is imported from anywhere.** `business_calendar.py` has
none -- it walks whatever periods it is given, which is the property that lets a
Saturday desk declare Saturdays. This module *builds* the Mon-Fri pattern
because it is the fixture's own choice, not the platform's.

**`as_business_calendar` mirrors a mapping that lives in production code.**
`ReturnCaseActivities._business_calendar` turns the configuration model into the
arithmetic's model, and it is private. Re-deriving it here is a duplication and
is recorded as one; what makes it safe is that
`test_business_hours_calendar_fixture.py` runs the real activity over a
configuration carrying this calendar and asserts the two answers are the same
instant. If the production mapping changes, the fixture fails rather than drifts.
"""

from __future__ import annotations

from datetime import date

import pytest

from return_platform.configuration.return_configuration import (
    BusinessCalendarConfiguration,
    BusinessWorkingPeriodConfiguration,
    ReturnPlatformConfiguration,
)
from return_platform.operations.business_calendar import BusinessCalendar, WorkingPeriod

__all__ = [
    "BUSINESS_HOURS_CALENDAR_ID",
    "DESK_OPENS_MINUTE",
    "DESK_TIMEZONE",
    "as_business_calendar",
    "business_hours_calendar",
    "business_hours_calendar_configuration",
    "nine_to_five_configuration",
    "with_business_calendar",
]

#: Deliberately not `default`. A scenario that meant to install this calendar
#: and did not would otherwise silently get the shipped 24/7 one and pass, which
#: is the exact failure this fixture exists to make impossible.
BUSINESS_HOURS_CALENDAR_ID = "acceptance-business-hours"

#: The desk's own zone, which is what `production.yaml` declares. A holiday and
#: an opening hour are local facts; the worker computing them may run anywhere.
DESK_TIMEZONE = "America/New_York"

DESK_OPENS_MINUTE = 9 * 60
DESK_CLOSES_MINUTE = 17 * 60

#: Monday is 0, matching `date.weekday()` and `BusinessWorkingPeriodConfiguration`.
_WEEKDAYS = (0, 1, 2, 3, 4)


def nine_to_five_configuration(
    *,
    calendar_id: str = BUSINESS_HOURS_CALENDAR_ID,
    timezone: str = DESK_TIMEZONE,
    holidays: tuple[date, ...] = (),
) -> BusinessCalendarConfiguration:
    """Mon-Fri 09:00-17:00, as a configuration release would declare it.

    Returns the *configuration* model rather than the arithmetic's model,
    because that is what a scenario has to install to test the real path: the
    workflow asks an activity, the activity reads
    `ReturnPlatformConfiguration.business_calendars`, and a fixture that handed
    out a `BusinessCalendar` directly would skip the half of the seam most
    likely to break.
    """
    return BusinessCalendarConfiguration(
        calendar_id=calendar_id,
        timezone=timezone,
        working_periods=tuple(
            BusinessWorkingPeriodConfiguration(
                weekday=weekday,
                start_minute=DESK_OPENS_MINUTE,
                end_minute=DESK_CLOSES_MINUTE,
            )
            for weekday in _WEEKDAYS
        ),
        holidays=holidays,
    )


def as_business_calendar(
    declared: BusinessCalendarConfiguration, *, fallback_timezone: str = "UTC"
) -> BusinessCalendar:
    """The configuration model as `advance_business_time` wants it.

    The calendar's own zone wins and `fallback_timezone` applies only to a
    calendar that declares none -- the same precedence
    `ReturnCaseActivities._business_calendar` uses, and the reason the
    equivalence test in this package's tests exists.
    """
    return BusinessCalendar(
        calendar_id=declared.calendar_id,
        timezone=declared.timezone or fallback_timezone,
        working_periods=tuple(
            WorkingPeriod(
                weekday=period.weekday,
                start_minute=period.start_minute,
                end_minute=period.end_minute,
            )
            for period in declared.working_periods
        ),
        holidays=frozenset(declared.holidays),
    )


def with_business_calendar(
    configuration: ReturnPlatformConfiguration,
    declared: BusinessCalendarConfiguration,
) -> ReturnPlatformConfiguration:
    """`configuration` with `declared` added and the case timings pointed at it.

    Both halves, because either alone is a no-op: a calendar nothing names is
    never consulted, and a `business_calendar_id` naming nothing falls back to
    wall clock and logs `business_calendar_not_configured` -- which is a
    legitimate production behaviour and therefore an entirely silent way for a
    business-time scenario to test nothing at all.

    A calendar already declared under the same id is replaced rather than
    appended: `_business_calendar` returns the first match, so appending a
    second `default` would install a calendar that never wins.

    `model_copy` rather than a dump-and-revalidate round trip. The values being
    substituted are already-validated model instances, and re-validating an
    entire `ReturnPlatformConfiguration` through `model_dump` would make this
    helper fail on any field whose python form does not round-trip -- a failure
    about pydantic, in a fixture about calendars.
    """
    others = tuple(
        existing
        for existing in configuration.business_calendars
        if existing.calendar_id != declared.calendar_id
    )
    return configuration.model_copy(
        update={
            "business_calendars": (*others, declared),
            "return_case": configuration.return_case.model_copy(
                update={"business_calendar_id": declared.calendar_id}
            ),
        }
    )


@pytest.fixture
def business_hours_calendar_configuration() -> BusinessCalendarConfiguration:
    """The Mon-Fri 09:00-17:00 desk, as configuration."""
    return nine_to_five_configuration()


@pytest.fixture
def business_hours_calendar(
    business_hours_calendar_configuration: BusinessCalendarConfiguration,
) -> BusinessCalendar:
    """The same desk, as the pure arithmetic takes it.

    Derived from the configuration fixture rather than built independently, so
    the two can never describe different desks.
    """
    return as_business_calendar(business_hours_calendar_configuration)
