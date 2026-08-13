"""One clock read per turn, one time zone, and the absolute windows they imply.

R3 wants "orders from last week" to work. Nothing in the reasoning loop knew
what week it was: `AgentTurnContext` carried thirteen fields and not one of them
was a date, so the only "now" available to the model was whatever its training
data suggested, and two calls inside the same turn could disagree about it.

Three things follow from that, and this module is all three.

**The clock is read once.** `as_of` is pinned at the start of a reasoning
attempt and carried in graph state, not re-read at each `_build_context`. A turn
whose first query means one "yesterday" and whose second means another is not a
turn that can be explained afterwards, and `_build_context` runs up to seven
times per turn.

**The boundaries are computed here, not by the model.** A model asked to turn
"last week" into a UTC range is doing calendar arithmetic across a time-zone
offset in prose, which it will usually get right. `resolve_date_windows` does it
in Python instead, so the model's job shrinks to picking a named window that is
already absolute. This is what makes "relative phrases convert to absolute
boundaries" a property of the platform rather than a hope about the prompt.

**Windows are half-open, `[start, end_exclusive)`.** A closed upper bound has to
name the last representable instant of a day, which is a different value at
second, millisecond and microsecond precision and silently drops rows in
whichever one the store does not use.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "DEFAULT_SESSION_TIMEZONE",
    "RELATIVE_DATE_PHRASES",
    "normalize_session_timezone",
    "resolve_date_windows",
    "temporal_grounding_prompt",
]

#: Used when the caller sends no time zone, or sends one this host cannot
#: resolve. UTC rather than a Ferguson branch default on purpose: a wrong
#: concrete zone shifts every boundary by hours while still looking authoritative,
#: whereas UTC is the one answer that is obviously an absence of information.
DEFAULT_SESSION_TIMEZONE = "UTC"

#: The phrases `resolve_date_windows` answers, in the exact spelling the model is
#: told to use. Deliberately short: every entry is a window an associate actually
#: says out loud while looking for an order, and each one added is another key in
#: every prompt for the rest of the platform's life.
RELATIVE_DATE_PHRASES: tuple[str, ...] = (
    "today",
    "yesterday",
    "this_week",
    "last_week",
    "this_month",
    "last_month",
    "last_7_days",
    "last_30_days",
)


def normalize_session_timezone(value: str | None) -> str:
    """An IANA zone name this host can actually load, or `DEFAULT_SESSION_TIMEZONE`.

    Never raises. The time zone arrives from a browser
    (`Intl.DateTimeFormat().resolvedOptions().timeZone`), and a client that ships
    a zone name this host's tz database has not heard of is a reason to ground
    the turn in UTC, not a reason to fail the associate's question.
    """
    if not value:
        return DEFAULT_SESSION_TIMEZONE
    candidate = value.strip()
    if not candidate:
        return DEFAULT_SESSION_TIMEZONE
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_SESSION_TIMEZONE
    return candidate


def _zone(session_timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(session_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_SESSION_TIMEZONE)


def _instant(day: date, zone: ZoneInfo) -> str:
    """Local midnight starting `day`, expressed as a UTC instant.

    Going through the local zone is the whole point: midnight in Asia/Kolkata is
    18:30 UTC the previous day, and a boundary computed in UTC directly would
    silently answer a different question than the associate asked.
    """
    return (
        datetime(day.year, day.month, day.day, tzinfo=zone)
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _window(start: date, end_exclusive: date, zone: ZoneInfo) -> dict[str, str]:
    return {"start": _instant(start, zone), "endExclusive": _instant(end_exclusive, zone)}


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _previous_month_start(day: date) -> date:
    first = _month_start(day)
    return _month_start(first - timedelta(days=1))


def resolve_date_windows(as_of: datetime, session_timezone: str) -> dict[str, dict[str, str]]:
    """Every phrase in `RELATIVE_DATE_PHRASES` as an absolute UTC half-open range.

    Weeks start Monday (ISO-8601). "Last week" is therefore the seven days
    ending the Sunday before the current week began, not "the seven days before
    now" -- `last_7_days` is that, and the two are different questions an
    associate distinguishes without thinking about it.
    """
    zone = _zone(session_timezone)
    local_today = as_of.astimezone(zone).date()
    tomorrow = local_today + timedelta(days=1)
    week_start = local_today - timedelta(days=local_today.weekday())
    month_start = _month_start(local_today)
    previous_month_start = _previous_month_start(local_today)
    return {
        "today": _window(local_today, tomorrow, zone),
        "yesterday": _window(local_today - timedelta(days=1), local_today, zone),
        "this_week": _window(week_start, tomorrow, zone),
        "last_week": _window(week_start - timedelta(days=7), week_start, zone),
        "this_month": _window(month_start, tomorrow, zone),
        "last_month": _window(previous_month_start, month_start, zone),
        "last_7_days": _window(local_today - timedelta(days=7), tomorrow, zone),
        "last_30_days": _window(local_today - timedelta(days=30), tomorrow, zone),
    }


def temporal_grounding_prompt(as_of: datetime, session_timezone: str) -> str:
    """The per-turn block appended to the configured system prompt.

    It is *appended* rather than written into the packaged task prompt because
    `as_of` changes every turn: a configured prompt is one immutable string per
    release, so the only thing it could ever say is "a date is somewhere in the
    context". Keeping the variable text last also leaves the stable prefix intact
    for the provider-side caching W5.3 turns on.
    """
    stamp = as_of.astimezone(UTC).isoformat().replace("+00:00", "Z")
    phrases = ", ".join(RELATIVE_DATE_PHRASES)
    return (
        "TEMPORAL GROUNDING (this turn only)\n"
        f"- The current instant is {stamp}. Treat this as now. Do not infer the "
        "date from anything else, including your own training data.\n"
        f"- The associate's session time zone is {session_timezone}. Day, week "
        "and month boundaries are that zone's, not UTC's.\n"
        "- `asOf`, `sessionTimezone` and `resolvedDateWindows` in the turn "
        "context carry these same values.\n"
        f"- `resolvedDateWindows` already holds absolute UTC boundaries for: "
        f"{phrases}. When the associate uses one of those phrases, take the "
        "window from there instead of computing dates yourself.\n"
        "- Every date filter you emit must use absolute instants. A half-open "
        "range is `start` inclusive, `endExclusive` exclusive.\n"
        "- For a relative phrase with no matching window, compute the "
        "boundaries from the current instant in the session time zone and state "
        "the absolute range you used."
    )
