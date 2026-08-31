"""Acceptance items 13 and 19 -- the reminder cadence, on a desk that closes.

* **13** -- per-case cadence, at most `max_reminders` in total across N reviews
  (DR-7: one cadence, one deadline, a **case** total), and no duplicate
  reminders across a clarification round-trip.
* **19** -- a wait spanning non-business hours fires **no retroactive burst**:
  the weekend does not become three reminders delivered at once on Monday
  morning, and it does not become three reminders delivered on Saturday either.

**Why this module exists next to `test_support_template_review_gate.py`, which
already drives this loop.** That suite's `_GateActivities.resolve_business_deadline`
is a double:

    start = datetime.fromisoformat(request.from_iso)
    return ResolvedBusinessDeadline(
        instant_iso=(start + timedelta(seconds=request.working_seconds)).isoformat(),
        calendar_applied=True,
    )

Wall-clock addition, with `calendar_applied=True` **asserted by the double
rather than computed**. That is the right double for what those tests are
about -- they are about the map-based wait, the notice drain and the deadline
branch, and a real calendar would only add noise. But it means the cadence has
never once been computed on a calendar that shuts, and every business-time claim
in items 13 and 19 is a claim about a calendar that shuts.

So this module keeps that harness -- the same runtime substitution, the same
real `SupportTemplateGateService` over the real `ReviewAggregateStore` -- and
replaces exactly one thing: `resolve_business_deadline` is the **real activity**,
reading a release that carries the Mon-Fri 09:00-17:00 desk from
`tests/harness/business_calendars.py`.

**And it asserts the calendar was applied rather than assuming it.** ACC phase
1's RV round recorded the failure mode precisely: a scenario that simply never
installs the fixture inherits `business_calendars.default`, which
`production.yaml` ships as a 24/7 dev calendar, runs on wall clock, and stays
green while proving nothing -- every gap zero seconds wide, every weekend
assertion reduced to addition. Every scenario below therefore asserts
**`calendar_applied is True` on every resolution the run performed**, and
`not calendar.is_continuous` on the desk itself, before any timing assertion is
allowed to mean anything.

The clock starts **Friday 16:30 local**, half an hour before the desk closes for
the weekend. On the 24/7 calendar the first reminder leg (two hours) lands at
18:30 the same Friday; on this desk it lands Monday 10:30. That difference is
what makes every assertion here falsifiable, and it is why the start instant is
a fixture rather than a literal buried in a test.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from itertools import pairwise
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from return_platform.configuration.return_configuration import ReturnPlatformConfiguration
from return_platform.configuration.settings import Settings
from return_platform.operations.case_commands import (
    DurableCaseCommandStore,
    ensure_case_command_indexes,
)
from return_platform.operations.review_aggregate import (
    ReviewAggregateStore,
    ReviewState,
    TemplateReviewParkReason,
    ensure_review_indexes,
)
from return_platform.workflows import return_case_workflow as workflow_module
from return_platform.workflows.return_case_activities import ReturnCaseActivities
from return_platform.workflows.return_case_workflow import (
    ResolvedBusinessDeadline,
    ReturnCaseTimings,
    ReturnCaseWorkflow,
)
from tests.harness.business_calendars import (
    BUSINESS_HOURS_CALENDAR_ID,
    DESK_TIMEZONE,
    as_business_calendar,
    nine_to_five_configuration,
    with_business_calendar,
)

# The gate harness, reused rather than rebuilt. Copying `_Runtime` and
# `_GateActivities` here would create a second definition of "the shipped run
# loop, driven", and the two would drift on the first change to either.
from tests.test_support_template_review_gate import (  # noqa: PLC2701 - the harness is the point
    CASE_ID,
    REQUEST_ID,
    _gate_service,
    _GateActivities,
    _input,
    _notice,
    _Runtime,
    _Support,
    _timings,
)

_async = pytest.mark.asyncio

#: Friday 16:30 in the desk's own zone -- thirty minutes of business time left
#: before a 64-hour close. Chosen so the very first reminder leg has to cross
#: the weekend: a start in the middle of a Tuesday would let a wall-clock run
#: and a business-time run agree for the first two legs, and a scenario that
#: agrees with the thing it is contrasted against is not measuring anything.
FRIDAY_AFTERNOON = datetime(2026, 8, 28, 16, 30, tzinfo=ZoneInfo(DESK_TIMEZONE))

MONDAY = FRIDAY_AFTERNOON.date() + timedelta(days=3)


class _BusinessTimeActivities(_GateActivities):
    """`_GateActivities` with the real business-time arithmetic in place.

    One override. Everything else -- the render input assembly, the drafts, the
    real gate service behind them -- is inherited, because the point of this
    module is that only the clock changed.

    Every resolution is recorded, so a scenario can assert `calendar_applied` on
    **all** of them rather than on the first. A run does eight or nine of these
    (one deadline, one per reminder leg), and a calendar that stopped being
    found halfway through -- a release swapped mid-wait, a fallback taken on one
    leg -- would leave the early ones true and the late ones silently wall-clock.
    """

    def __init__(
        self, gate: Any, *, configuration: ReturnPlatformConfiguration, **kwargs: Any
    ) -> None:
        super().__init__(gate, **kwargs)
        self._real = ReturnCaseActivities(
            repository=cast(Any, None),
            support_service=cast(Any, None),
            configuration=lambda: configuration,
        )
        self.resolutions: list[ResolvedBusinessDeadline] = []
        self.requests: list[Any] = []

    async def resolve_business_deadline(self, request: Any) -> ResolvedBusinessDeadline:
        resolved = await self._real.resolve_business_deadline(request)
        self.requests.append(request)
        self.resolutions.append(resolved)
        return resolved


class _RecordingRuntime(_Runtime):
    """`_Runtime` whose clock starts at the close of business on a Friday.

    `_remind_reviewers` sends nothing -- deliberately, and the workflow's own
    docstring explains why: there is no Support thread during this wait and
    opening one would be the gate defeating itself. Its whole observable is the
    log line and the durable counter, so the logger is captured here and the
    counter is read off the workflow instance.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.instant = FRIDAY_AFTERNOON
        #: `(when it fired, what it said)` for every reminder leg.
        self.reminders: list[tuple[datetime, str]] = []
        self.logger = _CapturingLogger(self.reminders, self)


class _CapturingLogger:
    """Just the one method the workflow calls, with the clock reading beside it.

    **The instant is taken from the runtime's own clock at the moment the
    reminder is logged, not from the deadlines the test watched go past.** Those
    two are different instruments and only one survives the fault this scenario
    exists to catch: an implementation that computed the reminder tick by
    wall-clock addition would stop asking `resolve_business_deadline` for legs
    altogether, and a test reading legs out of the resolution log would then find
    an empty list and fail with "nothing was resolved" rather than with "a
    reminder fired on Saturday".

    Nor is this the instrument flattening into the answer. The test never chooses
    how far the clock moves: the runtime advances by whatever timeout the gate
    passes to `wait_condition`, which the gate computes from the business
    deadline it resolved. The clock is production's arithmetic, observed.
    """

    def __init__(self, sink: list[tuple[datetime, str]], clock: Any) -> None:
        self._sink = sink
        self._clock = clock
        self._real = logging.getLogger("tests.acceptance.cadence")

    def info(self, message: str, *args: Any) -> None:
        self._sink.append((self._clock.now(), message % args))
        self._real.debug(message, *args)

    def warning(self, message: str, *args: Any) -> None:
        # Not recorded. `info` is the reminder leg; a warning is the gate
        # ignoring a notice for a review it does not hold, which is what an
        # arrival below deliberately provokes.
        self._real.debug(message, *args)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def desk_configuration(configuration: ReturnPlatformConfiguration) -> ReturnPlatformConfiguration:
    """The released configuration with the Mon-Fri desk installed and selected.

    `with_business_calendar` does both halves -- declares the calendar and
    points `return_case.business_calendar_id` at it -- because either alone is a
    silent no-op: an undeclared id falls back to wall clock and logs
    `business_calendar_not_configured`, which is a legitimate production
    behaviour and therefore an entirely silent way to test nothing.
    """
    return with_business_calendar(configuration, nine_to_five_configuration())


@pytest.fixture
def support() -> _Support:
    return _Support()


def _desk_activities(
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    desk_configuration: ReturnPlatformConfiguration,
    support: _Support,
    *,
    request_ids: tuple[str, ...] = (REQUEST_ID,),
) -> _BusinessTimeActivities:
    """The gate's activity table with the real calendar arithmetic behind it.

    A plain function rather than a fixture: `request_ids` varies per scenario
    (one review or two), and threading that through a fixture would need either
    a custom marker -- refused by `--strict-markers` -- or indirect
    parametrisation, both of which put the interesting value further from the
    test that depends on it.
    """
    return _BusinessTimeActivities(
        _gate_service(store, mongo, test_settings, desk_configuration, support),
        configuration=desk_configuration,
        request_ids=request_ids,
    )


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    activities: _BusinessTimeActivities,
    timings: ReturnCaseTimings,
    *,
    arrivals: list[Callable[[], Any]] | None = None,
    holder: list[ReturnCaseWorkflow] | None = None,
    workflow_input: Any = None,
) -> tuple[ReturnCaseWorkflow, _RecordingRuntime]:
    runtime = _RecordingRuntime(activities.table(), arrivals=arrivals)
    monkeypatch.setattr(workflow_module, "workflow", runtime)
    instance = ReturnCaseWorkflow()
    if holder is not None:
        # So an arrival can reach the running instance -- the gate suite's own
        # `_run_gate` takes the same parameter for the same reason.
        holder.append(instance)
    instance._input = workflow_input or _input(timings)  # noqa: SLF001 - the run loop's own field
    instance._state.template_reviews = dict(  # noqa: SLF001
        instance._input.resumed_template_reviews  # noqa: SLF001
    )
    await instance._open_support(timings)  # noqa: SLF001
    return instance, runtime


def _assert_business_time_was_actually_used(
    activities: _BusinessTimeActivities, timings: ReturnCaseTimings
) -> None:
    """The guard ACC phase 1's RV round asked for, as a callable.

    Three statements, and each catches a different way of proving nothing:

    * the desk **closes** -- a 24/7 calendar satisfies every other assertion
      here by addition;
    * the run pointed at *this* desk -- a calendar declared and not selected is
      a calendar never consulted;
    * **every** resolution applied it -- `calendar_applied is False` is what
      `resolve_business_deadline` returns when nothing declares the id, and it
      returns wall clock alongside it, silently.
    """
    desk = as_business_calendar(nine_to_five_configuration())
    assert not desk.is_continuous, (
        "the acceptance desk is continuous -- it is the 24/7 dev calendar under "
        "another name, and every business-time assertion below is addition"
    )
    assert timings.business_calendar_id == BUSINESS_HOURS_CALENDAR_ID
    assert activities.resolutions, "no business deadline was resolved at all"
    assert all(resolved.calendar_applied for resolved in activities.resolutions), (
        "a resolution fell back to wall clock: "
        f"{[r.calendar_applied for r in activities.resolutions]}. "
        "`resolve_business_deadline` returns calendar_applied=False and a wall-clock "
        "instant when no calendar declares the id, so this run was partly or wholly "
        "off the desk while every timing assertion stayed green."
    )


# --------------------------------------------------------------------------- #
# Item 19 -- a wait across a closed weekend, and no burst on the far side
# --------------------------------------------------------------------------- #


@_async
async def test_a_wait_across_the_weekend_fires_no_retroactive_burst(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    desk_configuration: ReturnPlatformConfiguration,
    support: _Support,
) -> None:
    """Item 19. Sixty-four closed hours produce no reminders, and no catch-up.

    The failure this is written against is the one a wall-clock cadence
    produces on a desk that shuts: the wait resumes on Monday having "owed"
    thirty-two two-hour legs, and either delivers them at once or delivers the
    first three at 02:30, 04:30 and 06:30 on Saturday. Both are the same defect
    -- time the desk was shut counted as time the reviewer was ignoring you.
    """
    activities = _desk_activities(store, mongo, test_settings, desk_configuration, support)
    timings = _timings(desk_configuration)
    instance, runtime = await _run(monkeypatch, activities, timings)

    _assert_business_time_was_actually_used(activities, timings)

    assert runtime.reminders, "no reminder fired at all -- the wait ended before the cadence did"
    fired = [when for when, _line in runtime.reminders]
    for instant in fired:
        local = instant.astimezone(ZoneInfo(DESK_TIMEZONE))
        assert local.weekday() < 5, (
            f"a reminder was scheduled for {local:%A %H:%M} -- the desk is shut"
        )
        assert 9 <= local.hour < 17, (
            f"a reminder was scheduled for {local:%A %H:%M} -- outside desk hours"
        )
        assert local.date() >= MONDAY, (
            f"a reminder was scheduled for {local:%A %H:%M}, before the desk reopened"
        )

    # The burst assertion proper: reminder legs stay a reminder interval apart
    # in *business* time. A catch-up delivers them microseconds apart.
    for earlier, later in pairwise(fired):
        assert later - earlier >= timedelta(hours=1), (
            f"two reminders {later - earlier} apart -- that is a retroactive burst, not a cadence"
        )

    assert (
        instance._state.parked_reason == TemplateReviewParkReason.TEMPLATE_REVIEW_UNANSWERED.value
    )  # noqa: SLF001


@_async
async def test_the_same_wait_on_the_shipped_24_7_calendar_reminds_before_monday(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    configuration: ReturnPlatformConfiguration,
    support: _Support,
) -> None:
    """The contrast that makes the test above mean something.

    Not a second assertion about the product -- a **control**. If this ran the
    same way as the scenario above, the desk would not be doing anything and the
    weekend assertions would be passing on the calendar rather than because of
    it. `production.yaml`'s own comment says so outright: "every deadline and
    reminder computed on this calendar becomes wall clock too".

    So: the shipped release, unmodified, same start instant, same timings. Its
    first reminder lands on the Friday evening. The scenario above forbids
    exactly that.
    """
    activities = _BusinessTimeActivities(
        _gate_service(store, mongo, test_settings, configuration, support),
        configuration=configuration,
    )
    timings = _timings(configuration)
    _instance, runtime = await _run(monkeypatch, activities, timings)

    assert timings.business_calendar_id != BUSINESS_HOURS_CALENDAR_ID
    assert runtime.reminders, "the control fired no reminder either, so it controls for nothing"
    first = runtime.reminders[0][0].astimezone(ZoneInfo(DESK_TIMEZONE))
    assert first.weekday() == 4 and first.hour >= 17, (
        f"the shipped calendar put the first reminder at {first:%A %H:%M}; it is 24/7, so "
        "it should land the same Friday evening. If it does not, the contrast this "
        "control provides has evaporated and the weekend test is asserting nothing."
    )


# --------------------------------------------------------------------------- #
# Item 13 -- the cap is a case total, not a per-review one
# --------------------------------------------------------------------------- #


@_async
async def test_two_reviews_share_one_cadence_and_one_cap(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    desk_configuration: ReturnPlatformConfiguration,
    support: _Support,
) -> None:
    """Item 13, DR-7. Two open reviews, still at most `max_reminders` in total.

    The defect this is written against is the obvious implementation: a cadence
    per review, which doubles the nudges for a case that happens to have been
    grouped into two requests -- and does it invisibly, because each review's
    own count is within its cap.
    """
    activities = _desk_activities(
        store,
        mongo,
        test_settings,
        desk_configuration,
        support,
        request_ids=(f"{REQUEST_ID}:a", f"{REQUEST_ID}:b"),
    )
    timings = _timings(desk_configuration)
    instance, runtime = await _run(monkeypatch, activities, timings)

    _assert_business_time_was_actually_used(activities, timings)

    assert len(instance._state.template_reviews) == 2  # noqa: SLF001
    sent = instance._state.template_review_reminders_sent  # noqa: SLF001
    assert sent == timings.template_review_max_reminders == 3
    assert len(runtime.reminders) == sent, (
        f"{len(runtime.reminders)} reminder legs logged but the durable counter says "
        f"{sent} -- the count an operator reads on the panel and the nudges that "
        "actually went out are different numbers"
    )

    # DR-7's other half: one reminder names *all* pending reviews rather than
    # each review getting its own leg.
    for _when, line in runtime.reminders:
        assert f"{REQUEST_ID}:a" in line and f"{REQUEST_ID}:b" in line, (
            f"a reminder leg named only some of the pending reviews: {line!r}"
        )


@_async
async def test_waking_the_wait_repeatedly_spends_no_extra_reminder(
    monkeypatch: pytest.MonkeyPatch,
    test_settings: Settings,
    desk_configuration: ReturnPlatformConfiguration,
) -> None:
    """Item 13's second half: no duplicate reminders across a round-trip.

    A clarification answered, a notice landing, an autosave -- anything that
    wakes the wait without the timer expiring must not cost a reminder leg. An
    associate who answers a clarification and then waits should not be nudged
    twice for it.

    **Asserted as a comparison, not as a ceiling.** The obvious form --
    "reminders <= max_reminders" -- is unfalsifiable here: the cap clamps the
    count whatever charges it, so an implementation that spent a leg on every
    wake would exhaust the cadence early and *still* satisfy a ceiling. Injected
    and confirmed. So the run is done twice, identical but for the wakes, and
    the two counts must match.
    """
    running: list[ReturnCaseWorkflow] = []

    async def _count(arrivals: list[Callable[[], Any]] | None) -> tuple[int, int]:
        # A fresh store per run. The two runs are the same case answered twice,
        # and sharing one would have the second collide on the first's review
        # rows -- which is a fact about the fixture, not about the cadence.
        del running[:]
        run_mongo, run_store = await _fresh_store(test_settings)
        activities = _desk_activities(
            run_store,
            run_mongo,
            test_settings,
            desk_configuration,
            _Support(),
        )
        timings = _timings(desk_configuration)
        instance, runtime = await _run(
            monkeypatch, activities, timings, arrivals=arrivals, holder=running
        )
        _assert_business_time_was_actually_used(activities, timings)
        return (
            instance._state.template_review_reminders_sent,  # noqa: SLF001
            len(runtime.reminders),
        )

    woken: list[int] = []

    def _wake() -> None:
        """A real wake: the wait's predicate becomes true and nothing settles.

        **The first form of this could not fail, and an injection proved it.**
        It appended to a counter and left the predicate false -- so the harness
        raised `TimeoutError` exactly as if nothing had arrived, and a
        production edit charging a reminder on the satisfied-predicate path
        (`self._state.template_review_reminders_sent += 1` above the `continue`)
        left all nine tests green. The wake has to reach the condition the gate
        is waiting on, which means putting something in
        `pending_template_notices`; a notice naming a review this case does not
        hold is drained and ignored, so the review stays open and the loop goes
        round again. That is a clarification round-trip's shape exactly: woken,
        nothing settled, carry on waiting.
        """
        woken.append(1)
        running[0]._state.pending_template_notices.append(  # noqa: SLF001
            ("approved", _notice(f"review-nobody-holds-{len(woken)}"))
        )

    undisturbed = await _count(None)
    disturbed = await _count([_wake for _ in range(6)])

    assert woken, "no wake actually ran, so the two runs are the same run twice"
    assert undisturbed[0] > 0, "the undisturbed run sent no reminders, so there is no baseline"
    assert disturbed == undisturbed, (
        f"waking the wait changed the cadence: {disturbed[0]} reminders with wakes against "
        f"{undisturbed[0]} without. A wake is not a timeout, and charging one spends the "
        "case's cadence on the associate having answered."
    )
    assert disturbed[1] == disturbed[0], (
        "the durable counter and the reminders actually logged disagree -- the number an "
        "operator reads on the panel is not the number of nudges that went out"
    )


# --------------------------------------------------------------------------- #
# The reviews the gate closed over
# --------------------------------------------------------------------------- #


@_async
async def test_the_weekend_wait_leaves_no_review_without_a_legal_exit(
    monkeypatch: pytest.MonkeyPatch,
    store: ReviewAggregateStore,
    mongo: Any,
    test_settings: Settings,
    desk_configuration: ReturnPlatformConfiguration,
    support: _Support,
) -> None:
    """AMENDMENT-5 rule 2, on the business-time path specifically.

    The rule is asserted in V1's own suite on the wall-clock path. It is
    re-asserted here because the deadline branch reached here is reached
    through a *different* arithmetic -- the deadline instant comes from the
    calendar rather than from addition -- and "every review has a legal exit" is
    a property of the close, which is what a weekend-spanning wait ends in.
    """
    activities = _desk_activities(store, mongo, test_settings, desk_configuration, support)
    timings = _timings(desk_configuration)
    await _run(monkeypatch, activities, timings)
    _assert_business_time_was_actually_used(activities, timings)

    reviews = await _reviews(store)
    assert reviews, "the gate closed over no reviews at all"
    stranded = [
        review
        for review in reviews
        if ReviewState(str(review["state"])) not in _STATES_WITH_AN_EXIT
    ]
    assert not stranded, (
        "a review was left in a state with no legal exit: "
        f"{[(r['_id'], r['state']) for r in stranded]}"
    )
    assert all(
        ReviewState(str(review["state"])) is ReviewState.HELD_FOR_OPERATIONS for review in reviews
    )


#: Every state a closed gate may leave a review in. `HELD_FOR_OPERATIONS` exits
#: to `OPEN` or `ABANDONED`; the three terminal ones need no exit. `OPEN` and
#: `APPROVING` are absent on purpose -- that is the trap AMENDMENT-5 closed.
_STATES_WITH_AN_EXIT = frozenset(
    {
        ReviewState.HELD_FOR_OPERATIONS,
        ReviewState.SENT,
        ReviewState.CANCELLED,
        ReviewState.ABANDONED,
        ReviewState.DELIVERY_FAILED,
    }
)


async def _fresh_store(test_settings: Settings) -> tuple[Any, ReviewAggregateStore]:
    """A store nobody else has written to, built the way the gate suite builds one."""
    from tests.operations.mongo_double import FakeClient

    client = FakeClient()
    database = client[test_settings.mongo_database]
    await ensure_review_indexes(database)
    await ensure_case_command_indexes(database)
    return client, ReviewAggregateStore(
        client, test_settings, command_store=DurableCaseCommandStore(client, test_settings)
    )


async def _reviews(store: ReviewAggregateStore) -> list[dict[str, Any]]:
    return await store.list_reviews(CASE_ID)
