"""Business-time arithmetic (SLA-01, contract C8).

`ReturnCaseTimingConfiguration` documented `support_response_wait_seconds` and
`reminder_interval_seconds` as business-calendar durations -- "eight hours means
eight *working* hours" -- and `business_calendar_id` and `timezone` were carried
all the way onto `ReturnCaseTimings`. A repository-wide search for either name
under `workflows/` returned exactly those two declaration lines. Nothing read
them. `deadline = workflow.now() + timedelta(seconds=...)` was wall-clock, so a
return raised at 16:30 on a Friday with an eight-hour wait, two-hour reminders
and a cap of three sent its reminders at 18:30, 20:30 and 22:30 into an empty
Friday-night queue and parked itself for operations at 00:30 on Saturday.

**No hardcoded Mon-Fri.** A calendar declares its working periods, one per
weekday, and this walks whatever is declared. A deployment whose warehouse runs
Saturdays says so in configuration and gets Saturdays; one that runs 24/7
declares every day whole and gets wall-clock behaviour back, exactly.

**Pure, and deliberately not workflow code.** Every function here takes its
calendar as an argument and returns a value. It resolves a timezone, which
reads the tz database -- `SubmitOrderDiscoveryTurnCommand` already documents
why that must never happen in a workflow body ("a determinism hazard the moment
the tz database on the worker changes"). So this runs inside an activity, and
the workflow receives absolute UTC instants that its history records. That is
the same boundary Track E's configuration adoption keeps: live configuration is
read on the activity side, and the workflow holds only what was pinned.

**DST is handled by construction.** Each working period is built as a local
wall-clock time on its own day and then converted, so the day a zone shifts is
23 or 25 hours long without anything here knowing it. The `fold` attribute
resolves the repeated hour to the first occurrence, which is the earlier real
instant and therefore the conservative deadline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "MAX_HORIZON_DAYS",
    "BusinessCalendar",
    "WorkingPeriod",
    "advance_business_time",
    "is_working_time",
]

#: How far ahead a deadline may be pushed before the calendar is declared
#: unusable. A calendar with no working period at all -- every day empty, or a
#: holiday list that swallows the year -- would otherwise walk forever looking
#: for a working second that does not exist. Two years is far past any
#: support SLA and short enough to fail visibly.
MAX_HORIZON_DAYS = 730


@dataclass(frozen=True, slots=True)
class WorkingPeriod:
    """One span of working time on one weekday, in the calendar's own zone.

    `weekday` is `date.weekday()`: Monday is 0. `end_minute` is exclusive and
    may be 1440, which is midnight at the end of the day -- expressing it as
    "24:00" rather than as 00:00 of the following day is what lets two
    consecutive whole days join into one continuous span instead of leaving a
    zero-length seam between them.
    """

    weekday: int
    start_minute: int
    end_minute: int

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise ValueError("weekday must be 0 (Monday) through 6 (Sunday)")
        if not 0 <= self.start_minute < self.end_minute <= 1440:
            raise ValueError("a working period must start before it ends, within one day")


@dataclass(frozen=True, slots=True)
class BusinessCalendar:
    """Working time for one calendar id.

    `holidays` are whole non-working days in the calendar's own zone, so a
    holiday is the same day for everyone reading this calendar regardless of
    where the worker computing it runs.
    """

    calendar_id: str
    timezone: str
    working_periods: tuple[WorkingPeriod, ...]
    holidays: frozenset[date] = frozenset()

    @property
    def is_continuous(self) -> bool:
        """True when every day is whole and nothing is a holiday.

        A 24/7 calendar must behave exactly like the wall clock, and saying so
        here lets `advance_business_time` return the trivial answer rather than
        walking 365 identical days to arrive at it.
        """
        if self.holidays:
            return False
        whole = {
            period.weekday
            for period in self.working_periods
            if period.start_minute == 0 and period.end_minute == 1440
        }
        return whole == {0, 1, 2, 3, 4, 5, 6}

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown business calendar timezone: {self.timezone}") from error


def _periods_on(calendar: BusinessCalendar, day: date) -> tuple[WorkingPeriod, ...]:
    if day in calendar.holidays:
        return ()
    return tuple(
        sorted(
            (period for period in calendar.working_periods if period.weekday == day.weekday()),
            key=lambda period: period.start_minute,
        )
    )


def _instant(day: date, minute: int, zone: ZoneInfo) -> datetime:
    """A local wall-clock minute on one day, as an absolute instant.

    `fold=0` picks the first occurrence of a repeated local time when a zone
    falls back, which is the earlier real instant. A deadline that resolves
    earlier is the conservative one: the case is chased sooner rather than
    later than the calendar promises.
    """
    if minute >= 1440:
        return datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone).astimezone(UTC)
    local = datetime.combine(
        day, time(hour=minute // 60, minute=minute % 60), tzinfo=zone
    ).replace(fold=0)
    return local.astimezone(UTC)


def is_working_time(calendar: BusinessCalendar, instant: datetime) -> bool:
    """Whether the calendar is open at this instant."""
    zone = calendar.zone()
    local_day = instant.astimezone(zone).date()
    # The previous day's periods can extend to midnight and so cover an instant
    # that belongs to this local day only by a hair.
    for day in (local_day - timedelta(days=1), local_day):
        for period in _periods_on(calendar, day):
            if _instant(day, period.start_minute, zone) <= instant < _instant(
                day, period.end_minute, zone
            ):
                return True
    return False


def advance_business_time(
    calendar: BusinessCalendar, start: datetime, working_seconds: int
) -> datetime:
    """`start` plus `working_seconds` of the calendar's working time.

    Counting begins at `start` if the calendar is open then, and at the next
    opening otherwise -- so a return raised at 16:30 on a Friday against a
    Mon-Fri 09:00-17:00 calendar consumes its first thirty minutes that
    afternoon and the rest from Monday morning.

    A non-positive duration returns `start` unchanged rather than the next
    opening: zero working seconds from a Friday evening is that Friday evening,
    and rounding it forward to Monday would move a deadline nobody asked to
    move.
    """
    if start.tzinfo is None:
        raise ValueError("start must be timezone-aware")
    start = start.astimezone(UTC)
    if working_seconds <= 0:
        return start
    if calendar.is_continuous:
        return start + timedelta(seconds=working_seconds)

    zone = calendar.zone()
    remaining = timedelta(seconds=working_seconds)
    day = start.astimezone(zone).date() - timedelta(days=1)
    horizon = start.astimezone(zone).date() + timedelta(days=MAX_HORIZON_DAYS)
    while day <= horizon:
        for period in _periods_on(calendar, day):
            opens = _instant(day, period.start_minute, zone)
            closes = _instant(day, period.end_minute, zone)
            if closes <= start:
                continue
            cursor = max(opens, start)
            available = closes - cursor
            if available >= remaining:
                return cursor + remaining
            remaining -= available
        day += timedelta(days=1)
    raise ValueError(
        f"business calendar {calendar.calendar_id} has no working time within "
        f"{MAX_HORIZON_DAYS} days of {start.isoformat()}"
    )
