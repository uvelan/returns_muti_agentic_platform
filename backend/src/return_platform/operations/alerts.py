"""Six operational conditions, evaluated and named.

The platform had no alerting of any kind -- no exporter, no counters, no
thresholds -- so every one of these was a thing an operator could only discover
by looking. The two-day housekeeping outage that lost `integration-outbox-worker`
is the shape of the problem: nothing was wrong with any *surface*, and nothing
said so.

**These are checks, not a metrics stack.** Choosing an exporter and a backend is
an operator's decision and not one to make on their behalf, so this reuses the
authority that already exists: `HardeningCheck` has a fixed id, a PASS/WARN/FAIL
vocabulary, a rolled-up status and an HTTP route. A second observability surface
beside it would be exactly the duplicate authority this programme has spent its
effort removing.

**Bounded cardinality is structural here, not a convention.** There are six
alerts because `ALERTS` has six entries; an alert is per *condition*, never per
entity, so a thousand stale runs are one firing alert with a count in its detail
rather than a thousand alerts. That is the property that makes this safe to wire
to a pager later.

**A reading that could not be taken is `NOT_VALIDATED`, never `PASS`.** Two of
the six have no counter to read yet -- workflow replay failures and
return-record/projection divergence -- and reporting those green because nothing
looked would be worse than reporting nothing at all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

AlertStatus = Literal["PASS", "WARN", "FAIL", "NOT_VALIDATED"]


@dataclass(frozen=True, slots=True)
class OperationalReading:
    """What was measured. `None` means "not measured", which is not zero.

    Every field is optional because the readings come from different subsystems
    and any of them may be uncomposed in a given process. A missing reading
    produces `NOT_VALIDATED` rather than a passing check -- the distinction
    between "healthy" and "unobserved" is the entire point of this module.
    """

    #: Documents in `integration_outbox` not yet published.
    outbox_depth: int | None = None
    #: `graph_sync_runs` rows still RUNNING past the stall cutoff.
    stalled_sync_runs: int | None = None
    #: Age in seconds of the oldest item still waiting on a human.
    oldest_queued_seconds: float | None = None
    #: Whether exactly one `ActiveRuntimeSnapshot` is serving.
    serving_snapshots: int | None = None
    #: Workflow replays that failed since the last evaluation.
    replay_failures: int | None = None
    #: Return records whose derived projection has not converged.
    unconverged_projections: int | None = None


@dataclass(frozen=True, slots=True)
class Alert:
    """One condition, with the thresholds that decide what it says."""

    id: str
    #: Reads the value this alert is about, or `None` if it was not measured.
    read: Callable[[OperationalReading], float | None]
    #: At or above this, WARN. Below it, PASS.
    warn_at: float
    #: At or above this, FAIL.
    fail_at: float
    #: What the number means, for the check's `details`.
    describe: Callable[[float], str]
    #: Why there is no reading, when there is none.
    unmeasured: str


def _status(value: float, alert: Alert) -> AlertStatus:
    if value >= alert.fail_at:
        return "FAIL"
    if value >= alert.warn_at:
        return "WARN"
    return "PASS"


#: The six conditions, and nothing else.
#:
#: Thresholds are deliberately coarse. An alert that fires on a number nobody
#: can act on trains operators to ignore it, which is worse than the silence
#: this replaces -- so `warn_at` is "look at this today" and `fail_at` is
#: "something is broken now".
ALERTS: Final[tuple[Alert, ...]] = (
    Alert(
        id="outbox-depth",
        read=lambda reading: reading.outbox_depth,
        # A publisher keeps this near zero. Hundreds means it has stopped.
        warn_at=100,
        fail_at=1_000,
        describe=lambda value: f"{int(value)} unpublished outbox events.",
        unmeasured="No Mongo client is composed in this process.",
    ),
    Alert(
        id="stalled-sync-runs",
        read=lambda reading: reading.stalled_sync_runs,
        # One is a process that died; the reclaimer terminalizes it within a
        # TTL. Several at once means the reclaimer is not running.
        warn_at=1,
        fail_at=5,
        describe=lambda value: f"{int(value)} sync runs past the stall cutoff.",
        unmeasured="No sync run ledger is composed in this process.",
    ),
    Alert(
        id="queue-age",
        read=lambda reading: reading.oldest_queued_seconds,
        # Work held for a human. An hour is a slow day; four is nobody looking.
        warn_at=3_600,
        fail_at=14_400,
        describe=lambda value: f"Oldest held item is {int(value // 60)} minutes old.",
        unmeasured="No interception store is composed in this process.",
    ),
    Alert(
        id="serving-snapshot",
        read=lambda reading: (
            None if reading.serving_snapshots is None else abs(reading.serving_snapshots - 1)
        ),
        # Exactly one snapshot serves. Zero means discovery has nothing to read
        # and two means it is ambiguous which -- both are distance from one.
        warn_at=1,
        fail_at=1,
        describe=lambda value: (
            "Exactly one active runtime snapshot."
            if value == 0
            else "The number of active runtime snapshots is not one."
        ),
        unmeasured="No snapshot store is composed in this process.",
    ),
    Alert(
        id="replay-failures",
        read=lambda reading: reading.replay_failures,
        # Any replay failure is a wedged workflow. There is no healthy number
        # above zero, which is why WARN and FAIL are both 1.
        warn_at=1,
        fail_at=1,
        describe=lambda value: f"{int(value)} workflow replays failed.",
        unmeasured=(
            "Replay failures are not counted yet; the worker logs them and nothing aggregates them."
        ),
    ),
    Alert(
        id="return-projection-convergence",
        read=lambda reading: reading.unconverged_projections,
        # SQL is authoritative and Mongo is derived, so a projection lagging
        # briefly is normal and a persistent gap is a lost outbox event.
        warn_at=1,
        fail_at=25,
        describe=lambda value: f"{int(value)} return records without a converged projection.",
        unmeasured=(
            "Projection convergence is not counted yet; it needs a query across "
            "SQL and Mongo that nothing runs on a schedule."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class AlertResult:
    id: str
    status: AlertStatus
    details: str
    #: The measured value, or `None`. Kept so a caller can chart it without
    #: re-deriving it from the prose.
    value: float | None


def evaluate(reading: OperationalReading) -> tuple[AlertResult, ...]:
    """Every alert, in a fixed order, whether or not it could be measured.

    Always the full set: an alert that disappears when its subsystem is absent
    is indistinguishable from one that is passing, and the absence is the more
    interesting fact.
    """
    results: list[AlertResult] = []
    for alert in ALERTS:
        value = alert.read(reading)
        if value is None:
            results.append(
                AlertResult(
                    id=alert.id,
                    status="NOT_VALIDATED",
                    details=alert.unmeasured,
                    value=None,
                )
            )
            continue
        results.append(
            AlertResult(
                id=alert.id,
                status=_status(value, alert),
                details=alert.describe(value),
                value=value,
            )
        )
    return tuple(results)
